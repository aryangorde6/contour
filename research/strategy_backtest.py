"""Does the strategy make money? Real option prices, the agent's real code.

This is not a reimplementation. It imports `select.choose_structure`,
`structures.assemble`, `structures.build` and `manage.should_exit` and drives
them, so what is measured here is the code that trades, not a paraphrase of it.

Method. One cycle per weekly expiry per name, entered 10 calendar days before
expiry -- the same offset the live agent runs (2026-09-11 expiry decided on
2026-09-01). At the decision bar we solve implied vol from each contract's
close, derive delta from it, build the same Measurement the live loop builds,
and let the structure map choose. The position is then walked forward day by
day and `should_exit` is asked the same question the cycle asks it, using that
day's closes as the mark. Anything still open at expiry settles at intrinsic.

Honest limits, stated rather than buried:
  * Historical option bars carry a CLOSE, not a bid/ask. Mid is therefore
    approximated by the close, and the spread is charged separately as an
    explicit haircut (SLIP each way) rather than pretended away.
  * Exits are evaluated on daily closes. The live agent polls every 15 minutes,
    so it would catch some stops earlier and some profit targets earlier. Daily
    resolution cuts both ways and is not obviously conservative.
  * Each cycle is sized independently at the agent's per-position cap. The
    live book also has G3 book-risk and G4 concentration limits, which can only
    REDUCE deployment, so this is an upper bound on how much gets traded.
  * Alpaca's option history starts 2024-01-18, so the sample cannot include
    2022. That was the worst year for this kind of book and its absence is a
    real limitation of the sample, not of the strategy.
"""
from __future__ import annotations

import json
import math
import signal
import socket
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import NormalDist

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
load_dotenv()

# The failure that actually bit: `requests` sets no default timeout, so an
# ESTABLISHED socket that simply never answers blocks the run forever. It hung
# for 27 minutes on one QQQ contracts call with the main thread parked in
# wait_woken. Thirty seconds is far above any healthy response from this API.
socket.setdefaulttimeout(30)

from contour import config as C
from contour import profile as VP
from contour import select, surface
from contour import structures as S
from contour.manage import ManagedPosition, should_exit
from contour.models import Bar, Leg

NORM = NormalDist().cdf
RATE = 0.04
SLIP = 0.05          # 5% of the package price each way, charged explicitly
NAV = 100_000.0
BAND = 0.12
CACHE = Path(__file__).resolve().parents[1] / ".bt_cache"


def bs(spot, k, t, sig, call):
    if t <= 0 or sig <= 0:
        return max(0.0, (spot - k) if call else (k - spot))
    d1 = (math.log(spot / k) + (RATE + sig * sig / 2) * t) / (sig * math.sqrt(t))
    d2 = d1 - sig * math.sqrt(t)
    if call:
        return spot * NORM(d1) - k * math.exp(-RATE * t) * NORM(d2)
    return k * math.exp(-RATE * t) * NORM(-d2) - spot * NORM(-d1)


def solve_iv(price, spot, k, t, call):
    """Bisection. Returns None when the price is outside what any vol explains
    -- an arbitrage-violating print is dropped, never clamped into range."""
    if t <= 0 or price <= 0:
        return None
    intrinsic = max(0.0, (spot - k) if call else (k - spot))
    if price < intrinsic - 0.01:
        return None
    lo, hi = 1e-4, 5.0
    if bs(spot, k, t, hi, call) < price:
        return None
    for _ in range(80):
        mid = (lo + hi) / 2
        if bs(spot, k, t, mid, call) < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def delta_of(spot, k, t, sig, call):
    if t <= 0 or sig <= 0:
        return (1.0 if spot > k else 0.0) if call else (-1.0 if spot < k else 0.0)
    d1 = (math.log(spot / k) + (RATE + sig * sig / 2) * t) / (sig * math.sqrt(t))
    return NORM(d1) if call else NORM(d1) - 1.0


# --- data ------------------------------------------------------------------

def clients():
    from alpaca.data.historical.option import OptionHistoricalDataClient
    from alpaca.data.historical.stock import StockHistoricalDataClient
    from alpaca.trading.client import TradingClient
    k, s = os.environ["ALPACA_DEV_API_KEY"], os.environ["ALPACA_DEV_SECRET_KEY"]
    return (StockHistoricalDataClient(k, s), OptionHistoricalDataClient(k, s),
            TradingClient(k, s, paper=True))


def retry(fn, *a, **kw):
    for i in range(5):
        try:
            return fn(*a, **kw)
        except Exception as exc:                                  # noqa: BLE001
            # A 403 is a subscription boundary, not congestion. Retrying it
            # five times with backoff just spends 15 seconds to fail anyway.
            if "403" in str(exc) or "subscription" in str(exc).lower():
                raise
            if i == 4:
                raise
            time.sleep(2 ** i)
    return None


def stock_bars(stk, sym, start, end):
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    bs_ = retry(stk.get_stock_bars, StockBarsRequest(
        symbol_or_symbols=sym, timeframe=TimeFrame.Day, start=start, end=end))
    return [{"d": b.timestamp.date(), "o": float(b.open), "h": float(b.high),
             "l": float(b.low), "c": float(b.close), "v": float(b.volume)}
            for b in bs_.data[sym]]


def contracts_for(trd, und, expiry, lo, hi):
    from alpaca.trading.enums import AssetStatus
    from alpaca.trading.requests import GetOptionContractsRequest
    out, token = {}, None
    while True:
        res = retry(trd.get_option_contracts, GetOptionContractsRequest(
            underlying_symbols=[und], status=AssetStatus.INACTIVE,
            expiration_date=expiry, strike_price_gte=str(round(lo, 2)),
            strike_price_lte=str(round(hi, 2)), limit=1000, page_token=token))
        for c in res.option_contracts:
            out[c.symbol] = {
                "strike": float(c.strike_price),
                "type": c.type.value if hasattr(c.type, "value") else str(c.type),
                "oi": int(float(c.open_interest or 0)),
            }
        token = getattr(res, "next_page_token", None)
        if not token:
            return out


def option_bars(opt, syms, start, end):
    from alpaca.data.requests import OptionBarsRequest
    from alpaca.data.timeframe import TimeFrame
    out: dict[str, dict[date, float]] = {}
    for i in range(0, len(syms), 200):
        chunk = syms[i:i + 200]
        res = retry(opt.get_option_bars, OptionBarsRequest(
            symbol_or_symbols=chunk, timeframe=TimeFrame.Day,
            start=start, end=end))
        for sym, bars in res.data.items():
            out[sym] = {b.timestamp.date(): float(b.close) for b in bars}
    return out


def fridays(a: date, b: date):
    d = a + timedelta((4 - a.weekday()) % 7)
    while d <= b:
        yield d
        d += timedelta(7)


# --- one cycle -------------------------------------------------------------

def run_cycle(und, expiry, ubars, trd, opt, use_profile):
    """Returns a result dict, or None when the cycle is unmeasurable."""
    target = expiry - timedelta(days=10)
    hist = [b for b in ubars if b["d"] <= target]
    if len(hist) < 40:
        return None
    dec = hist[-1]["d"]
    spot = hist[-1]["c"]
    closes = [b["c"] for b in hist[-11:]]

    lo, hi = spot * (1 - BAND), spot * (1 + BAND)
    key = CACHE / f"{und}-{expiry}.json"
    if key.exists():
        blob = json.loads(key.read_text())
        meta = blob["meta"]
        marks = {s: {date.fromisoformat(k): v for k, v in m.items()}
                 for s, m in blob["marks"].items()}
    else:
        meta = contracts_for(trd, und, expiry, lo, hi)
        if not meta:
            return None
        marks = option_bars(opt, sorted(meta), dec, expiry + timedelta(days=1))
        CACHE.mkdir(exist_ok=True)
        key.write_text(json.dumps({
            "meta": meta,
            "marks": {s: {d.isoformat(): v for d, v in m.items()}
                      for s, m in marks.items()}}))

    t = (expiry - dec).days / 365.0
    legs = []
    for sym, m in meta.items():
        px = marks.get(sym, {}).get(dec)
        if not px or px <= 0.01:
            continue
        call = m["type"].lower().startswith("c")
        iv = solve_iv(px, spot, m["strike"], t, call)
        if iv is None or not (0.01 < iv < 3.0):
            continue
        legs.append(Leg(
            symbol=sym, side="buy", ratio_qty=1,
            option_type="call" if call else "put", strike=m["strike"],
            expiration_date=expiry, bid=px, ask=px,
            delta=delta_of(spot, m["strike"], t, iv, call),
            implied_volatility=iv, open_interest=m["oi"], tradable=True,
            close_price=px, quote_age_s=1.0))
    if len(legs) < 8:
        return None

    puts = [l for l in legs if l.option_type == "put"]
    calls = [l for l in legs if l.option_type == "call"]
    p25 = S.pick_by_delta(puts, 0.25, (0.18, 0.32))
    c25 = S.pick_by_delta(calls, 0.25, (0.18, 0.32))
    if p25 is None or c25 is None:
        return None
    m = surface.measure(und, spot, closes,
                        [(l.strike, l.implied_volatility) for l in legs],
                        p25.implied_volatility, c25.implied_volatility)

    structure, why = select.choose_structure(m)
    if structure == "NO_TRADE":
        return {"und": und, "expiry": expiry, "decided": dec,
                "structure": "NO_TRADE", "reason": why, "pnl": 0.0}

    prof = None
    if use_profile:
        window = [Bar(high=b["h"], low=b["l"], close=b["c"], volume=b["v"])
                  for b in hist[-C.PROFILE_LOOKBACK_D:]]
        prof = VP.value_area(und, window)

    sided, structure, note = S.assemble(structure, legs, und, prof)
    if not sided:
        return {"und": und, "expiry": expiry, "decided": dec,
                "structure": "NO_TRADE", "reason": note, "pnl": 0.0}
    cand = S.build(und, structure, sided, NAV)
    if cand is None:
        return {"und": und, "expiry": expiry, "decided": dec,
                "structure": "NO_TRADE", "reason": "unsizable", "pnl": 0.0}

    credit = cand.net_credit * (1 - SLIP)
    pos = ManagedPosition(candidate=cand, credit_received=credit,
                          opened_at=datetime(dec.year, dec.month, dec.day,
                                             tzinfo=C.ET), order_id="bt")
    # The scheduled flatten is a contest artifact with a fixed date; neutralise
    # it so the merit rules are what get measured.
    old_flat = C.FLATTEN_DAY
    C.FLATTEN_DAY = expiry + timedelta(days=1)
    try:
        exit_mark, exit_why, exit_on = None, "EXPIRY", expiry
        for b in [x for x in ubars if dec < x["d"] <= expiry]:
            d = b["d"]
            px = {}
            missing = False
            for l in cand.legs:
                v = marks.get(l.symbol, {}).get(d)
                if v is None:
                    missing = True
                    break
                px[l.symbol] = v
            if missing:
                continue
            mark = abs(sum((px[l.symbol] if l.is_short else -px[l.symbol])
                           for l in cand.legs))
            do, why2 = should_exit(pos, mark, b["c"], datetime(
                d.year, d.month, d.day, 16, tzinfo=C.ET))
            if do:
                exit_mark, exit_why, exit_on = mark, why2, d
                break
        if exit_mark is None:                       # settled at expiry
            settle = [x for x in ubars if x["d"] <= expiry][-1]["c"]
            exit_mark = abs(sum(
                (1 if l.is_short else -1) *
                max(0.0, (settle - l.strike) if l.option_type == "call"
                    else (l.strike - settle))
                for l in cand.legs))
    finally:
        C.FLATTEN_DAY = old_flat

    pnl = (credit - exit_mark * (1 + SLIP)) * 100.0 * cand.contracts
    return {"und": und, "expiry": expiry, "decided": dec, "structure": structure,
            "reason": why, "note": note, "contracts": cand.contracts,
            "credit": round(credit, 3), "exit_mark": round(exit_mark, 3),
            "exit_why": exit_why.split(":")[0], "exit_on": exit_on,
            "max_loss": cand.max_loss_per_contract * cand.contracts,
            "pnl": pnl}


def summarise(label, rows):
    traded = [r for r in rows if r["structure"] != "NO_TRADE"]
    if not traded:
        print(f"{label}: no trades"); return
    pnls = [r["pnl"] for r in traded]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    tot = sum(pnls)
    gp, gl = sum(wins), -sum(losses)
    mean = tot / len(pnls)
    sd = math.sqrt(sum((p - mean) ** 2 for p in pnls) / (len(pnls) - 1)) if len(pnls) > 1 else 0.0
    eq, peak, dd = 0.0, 0.0, 0.0
    for r in sorted(traded, key=lambda x: x["decided"]):
        eq += r["pnl"]; peak = max(peak, eq); dd = min(dd, eq - peak)
    print(f"\n=== {label} ===")
    print(f"  cycles evaluated   {len(rows)}   traded {len(traded)} "
          f"({len(traded)/len(rows):.0%})")
    print(f"  total P&L          ${tot:,.0f}   ({tot/NAV*100:+.2f}% of a $100k account)")
    print(f"  per trade          ${mean:,.0f}  sd ${sd:,.0f}")
    print(f"  win rate           {len(wins)/len(pnls):.1%}  ({len(wins)}W / {len(losses)}L)")
    print(f"  profit factor      {gp/gl:.2f}" if gl > 0 else "  profit factor      inf")
    print(f"  avg win / avg loss ${gp/len(wins) if wins else 0:,.0f} / "
          f"${gl/len(losses) if losses else 0:,.0f}")
    print(f"  worst trade        ${min(pnls):,.0f}")
    print(f"  max drawdown       ${dd:,.0f}")
    if sd > 0:
        print(f"  t-stat vs zero     {mean/(sd/math.sqrt(len(pnls))):+.2f}")
    by = {}
    for r in traded:
        by.setdefault(r["exit_why"], []).append(r["pnl"])
    print("  exits:", ", ".join(
        f"{k} {len(v)} (${sum(v):,.0f})" for k, v in sorted(by.items())))


def _timeout(signum, frame):
    raise TimeoutError("cycle exceeded its watchdog")


def main():
    stk, opt, trd = clients()
    start, end = date(2024, 1, 18), date(2026, 8, 28)
    rows_on, rows_off = [], []
    for und in C.UNIVERSE:
        ub = stock_bars(stk, und, datetime(2023, 10, 1), datetime(2026, 8, 31))
        print(f"{und}: {len(ub)} daily bars", file=sys.stderr)
        for exp in fridays(start, end):
            for use, sink in ((False, rows_off), (True, rows_on)):
                # Belt and braces: the socket timeout covers a stalled read,
                # this covers anything else that fails to return at all.
                signal.signal(signal.SIGALRM, _timeout)
                signal.alarm(90)
                try:
                    r = run_cycle(und, exp, ub, trd, opt, use)
                except Exception as exc:                          # noqa: BLE001
                    print(f"  {und} {exp} failed: {type(exc).__name__} "
                          f"{str(exc)[:60]}", file=sys.stderr)
                    continue
                finally:
                    signal.alarm(0)
                if r:
                    sink.append(r)
            print(f"  {und} {exp} done", file=sys.stderr)
    summarise("delta strikes only (the strategy as it shipped)", rows_off)
    summarise("delta strikes + volume-profile filter", rows_on)
    out = Path(__file__).resolve().parent / "strategy_backtest_trades.json"
    out.write_text(json.dumps(
        {"delta_only": rows_off, "with_profile": rows_on}, default=str, indent=1))
    print(f"\ntrades written to {out.name}")


if __name__ == "__main__":
    main()

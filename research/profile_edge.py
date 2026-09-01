"""Does the volume-profile value area predict short-strike breaches better
than distance alone? Matched-distance comparison, real bars, no options data
needed: a 0.13-delta short strike sits ~1.13 sigma out, so we hold distance
in sigma units FIXED and vary only inside/outside the value area.
"""
import os, math, sys
from datetime import datetime, timezone, timedelta
import numpy as np
from dotenv import load_dotenv

load_dotenv()
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

K = os.environ["ALPACA_DEV_API_KEY"]; S = os.environ["ALPACA_DEV_SECRET_KEY"]
cli = StockHistoricalDataClient(K, S)

LOOKBACK = 20      # profile window, trading days
HORIZON  = 8       # ~ the Sep-11 condor's life from Sep 1
VA_PCT   = 0.70    # value area = 70% of traded volume
BINS     = 240


def value_area(bars):
    """POC / VAH / VAL from OHLCV: each day's volume spread uniformly over
    its own high-low range, then expand out from the POC to VA_PCT."""
    lo = min(b["l"] for b in bars); hi = max(b["h"] for b in bars)
    if hi <= lo:
        return None
    edges = np.linspace(lo, hi, BINS + 1)
    mids = (edges[:-1] + edges[1:]) / 2.0
    vol = np.zeros(BINS)
    for b in bars:
        a, z = b["l"], b["h"]
        if z <= a:
            i = min(int((a - lo) / (hi - lo) * BINS), BINS - 1)
            vol[i] += b["v"]; continue
        w = np.clip((np.minimum(edges[1:], z) - np.maximum(edges[:-1], a)), 0, None)
        s = w.sum()
        if s > 0:
            vol += b["v"] * w / s
    poc_i = int(np.argmax(vol))
    total = vol.sum(); want = total * VA_PCT
    lo_i = hi_i = poc_i; got = vol[poc_i]
    while got < want and (lo_i > 0 or hi_i < BINS - 1):
        dn = vol[lo_i - 1] if lo_i > 0 else -1.0
        up = vol[hi_i + 1] if hi_i < BINS - 1 else -1.0
        if up >= dn:
            hi_i += 1; got += up
        else:
            lo_i -= 1; got += dn
    return mids[poc_i], mids[hi_i], mids[lo_i]   # POC, VAH, VAL


def load(sym, years=5):
    req = StockBarsRequest(symbol_or_symbols=sym, timeframe=TimeFrame.Day,
                           start=datetime.now(timezone.utc) - timedelta(days=365*years))
    bs = cli.get_stock_bars(req).data[sym]
    return [{"t": b.timestamp, "o": float(b.open), "h": float(b.high),
             "l": float(b.low), "c": float(b.close), "v": float(b.volume)} for b in bs]


def ztest(x1, n1, x2, n2):
    if n1 == 0 or n2 == 0:
        return float("nan")
    p1, p2 = x1/n1, x2/n2
    p = (x1+x2)/(n1+n2)
    se = math.sqrt(p*(1-p)*(1/n1+1/n2))
    return (p1-p2)/se if se > 0 else float("nan")


def main():
    GRID = [0.9, 1.0, 1.13, 1.25, 1.4]
    rows = {}   # (side, d) -> [in_breach, in_n, out_breach, out_n]

    for sym in ("SPY", "QQQ", "IWM"):
        bars = load(sym)
        print(f"{sym}: {len(bars)} daily bars  {bars[0]['t'].date()} -> {bars[-1]['t'].date()}",
              file=sys.stderr)
        closes = np.array([b["c"] for b in bars])
        rets = np.diff(np.log(closes))
        for t in range(LOOKBACK + 21, len(bars) - HORIZON):
            va = value_area(bars[t - LOOKBACK:t])
            if va is None:
                continue
            poc, vah, val = va
            sig = float(np.std(rets[t-20:t], ddof=1))
            if not sig > 0:
                continue
            spot = bars[t]["c"]
            fwd = bars[t+1:t+1+HORIZON]
            fhi = max(b["h"] for b in fwd); flo = min(b["l"] for b in fwd)
            move = sig * math.sqrt(HORIZON)
            for d in GRID:
                ku = spot * math.exp(+d*move); kd = spot * math.exp(-d*move)
                # call side: strike inside the value area = selling into traffic
                for side, k, breach, outside in (
                    ("call", ku, fhi >= ku, ku > vah),
                    ("put",  kd, flo <= kd, kd < val),
                ):
                    r = rows.setdefault((side, d), [0, 0, 0, 0])
                    if outside:
                        r[2] += int(breach); r[3] += 1
                    else:
                        r[0] += int(breach); r[1] += 1

    print(f"\n{'side':5} {'dist':>5} | {'INSIDE VA':>18} | {'OUTSIDE VA':>18} | {'edge':>7} {'z':>6}")
    print("-"*76)
    for (side, d), (ib, i_n, ob, o_n) in sorted(rows.items()):
        ip = ib/i_n if i_n else float('nan'); op = ob/o_n if o_n else float('nan')
        z = ztest(ib, i_n, ob, o_n)
        print(f"{side:5} {d:5.2f} | {ip:6.1%} ({ib:5d}/{i_n:5d}) | {op:6.1%} ({ob:5d}/{o_n:5d}) "
              f"| {ip-op:+6.1%} {z:+6.2f}")


if __name__ == "__main__":
    main()

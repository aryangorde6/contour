"""python -m contour [--once] [--dry] [--as-of ISO] [--verify] [--dev]
                    [--record PATH] [--replay [PATH]]"""
from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from . import config as C
from .data import AlpacaData
from .execute import CLIBroker
from .journal import Journal
from .loop import run_cycle
from .mind import Mind
from . import positions as P
from . import state
from .replay import Recorder, Replay, ReplayBroker, ReplayError
from .sleeve import SleevePosition


def _passed(reason: str) -> bool:
    r"""Gate reasons are journaled pass or fail, so the printer has to tell
    them apart. A pass always reads "G<n> ok"; the dashboard tests the same
    shape (/^G\d+ ok/), and the two must stay in step."""
    return re.match(r"^G\d+ ok\b", reason) is not None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="contour")
    ap.add_argument("--once", action="store_true", help="run one cycle (default)")
    ap.add_argument("--dry", action="store_true",
                    help="measure, select and gate, but submit nothing")
    ap.add_argument("--as-of", metavar="ISO",
                    help="override the clock (requires --dry)")
    ap.add_argument("--dev", action="store_true",
                    help="use the throwaway account, never the judged one")
    ap.add_argument("--verify", action="store_true",
                    help="recompute the journal hash chain and exit")
    ap.add_argument("--brain-check", action="store_true",
                    help="exercise all three LLM calls and exit; no trading")
    ap.add_argument("--record", metavar="PATH",
                    help="tee every market read into a replay fixture")
    ap.add_argument("--replay", metavar="PATH", nargs="?", const="",
                    help="run a recorded fixture; needs no credentials at all "
                         "(bare --replay picks the newest in fixtures/)")
    args = ap.parse_args(argv)

    load_dotenv()

    if args.brain_check:
        mind = Mind()
        print(f"brain: {mind.brain}")
        if not mind.configured:
            print("no provider configured -- set one of AWS_BEARER_TOKEN_BEDROCK,"
                  "\n  AWS_ACCESS_KEY_ID+AWS_SECRET_ACCESS_KEY, FEATHERLESS_API_KEY,"
                  "\n  GEMINI_API_KEY -- or check CONTOUR_LLM names one of them")
            return 1
        day = datetime.now(C.ET).date()
        ok = True
        for label, call in (
            ("blackouts", lambda: mind.blackouts(day)),
            ("regime", lambda: mind.regime(day, {"SPY": 1.42, "QQQ": 1.38, "IWM": 1.51})),
            ("confirm", lambda: mind.confirm("SPY", "CONDOR", 1.42, 0.1)),
        ):
            r = call()
            src = getattr(r, "source", None)
            bad = src == "failed_closed" or (src is None and r.veto
                                             and "fail-closed" in r.reason)
            ok &= not bad
            print(f"\n[{label}] {'FAIL' if bad else 'ok'}")
            print(f"  {r}")
        print(f"\n{'all three calls returned schema-valid output' if ok else 'BRAIN UNUSABLE -- see above'}")
        return 0 if ok else 1

    if args.replay is not None:
        try:
            fx = Replay.load(args.replay) if args.replay else Replay.newest()
        except ReplayError as exc:
            print(exc, file=sys.stderr)
            return 2
        return _run_replay(fx)

    if args.verify:
        ok_all = True
        for p in sorted(Path("journal").glob("*.jsonl")):
            ok, msg = Journal(p).verify()
            print(f"{'ok  ' if ok else 'FAIL'} {p}: {msg}")
            ok_all &= ok
        return 0 if ok_all else 1

    if args.as_of and not args.dry:
        ap.error("--as-of is a testing affordance and requires --dry")

    prefix = "ALPACA_DEV_" if args.dev else "ALPACA_"
    key, sec = os.getenv(prefix + "API_KEY"), os.getenv(prefix + "SECRET_KEY")
    if not key or not sec:
        print(f"missing {prefix}API_KEY / {prefix}SECRET_KEY", file=sys.stderr)
        return 2

    # The judged account number is committed; the dev account must be anything
    # else. This is the last line of defence against the profile-override trap.
    expected = Path("ACCOUNT_ID.txt").read_text().strip()
    broker = CLIBroker(api_key=key, secret_key=sec, expected_account=expected)
    if args.dev:
        got = str(broker.account().get("account_number"))
        if got == expected:
            print(f"REFUSING: --dev but credentials point at the JUDGED "
                  f"account {got}", file=sys.stderr)
            return 3
        broker.expected_account = got
        print(f"[dev] trading account {got}")
    else:
        print(f"[judged] trading account {broker.assert_account()}")

    now_et = (datetime.fromisoformat(args.as_of).replace(tzinfo=C.ET)
              if args.as_of else datetime.now(C.ET))
    market_open = bool(args.as_of) or _market_open(key, sec)

    journal = Journal(Path("journal") / f"{now_et:%Y-%m-%d}.jsonl")
    mind = Mind()
    print(f"[mind] brain: {mind.brain}")
    if not mind.configured:
        print("[mind] no LLM provider configured -- degraded: half size, "
              "hard-coded blackout table, no LLM veto")
    # The open book has to be reloaded every run: each cron cycle is a fresh
    # container, so a position opened at 10:09 is invisible at 10:24 unless it
    # was written down. Without this, every exit rule is dead code.
    open_positions = P.load()
    sleeve_pos = _reconcile_sleeve(broker, P.load_sleeve(), journal, now_et)
    held = _held_symbols(broker)
    tracked = {l.symbol for p in open_positions for l in p.candidate.legs}
    print(f"[book] {len(open_positions)} tracked position(s), "
          f"{len(held)} option leg(s) at the broker")
    if sleeve_pos is not None:
        print(f"[sleeve] {sleeve_pos.shares} {sleeve_pos.underlying} @ "
              f"${sleeve_pos.entry_price:.2f}, stop ${sleeve_pos.stop_price:.2f}"
              + ("" if sleeve_pos.stop_order_id else "  -- STOP NOT RESTING"))
    else:
        print(f"[sleeve] flat ({C.SLEEVE_UNDERLYING}, "
              f"${C.SLEEVE_NOTIONAL:,.0f} ceiling)")
    orphans = held - tracked
    if orphans:
        # Loud, not fatal: an unmanaged leg is exactly the failure this book
        # exists to prevent, and silence is how it stayed hidden.
        print(f"[book] WARNING: {len(orphans)} broker leg(s) not in the "
              f"tracked book: {sorted(orphans)}", file=sys.stderr)
        journal.append({"event": "book_discrepancy",
                        "untracked_legs": sorted(orphans),
                        "tracked": sorted(tracked)})

    ds = AlpacaData(key, sec)
    rec = Recorder(ds, args.record) if args.record else None
    # Counted from the last published heartbeat, not from this process: every
    # cron run is a fresh container, so an in-process counter reports 0 for
    # ever -- which is what the journal said for the whole first week.
    res = run_cycle(ds=rec or ds, broker=broker, now_et=now_et,
                    market_open=market_open, journal=journal, dry=args.dry,
                    mind=mind, open_positions=open_positions,
                    sleeve_position=sleeve_pos,
                    sleeve_retired=P.sleeve_retired(),
                    cycle=state.next_cycle())
    if rec is not None:
        print(f"\n[record] wrote {rec.save(now_et)}")

    print(f"\nmode={res.mode}  ({res.reason})")
    for m in res.measurements:
        print(f"  {m['underlying']}: spot {m['spot']}  atm_iv {m['atm_iv']:.1f}  "
              f"rv10 {m['rv10']:.1f}  vrp {m['vrp_ratio']:.2f}  "
              f"skew {m['skew25']:+.2f} (z {m['skew_z']:+.2f})")
    for d in res.decisions:
        print(f"  {d['underlying']}: {d['decision']} -- {d['reason']}")
        for g in d.get("gates", []):
            if not _passed(g):
                print(f"      VETO: {g}")
    _print_sleeve(res.sleeve)
    return 0


def _print_sleeve(sl: dict) -> None:
    if not sl:
        return
    chk = sl.get("exit_check")
    if chk:
        print(f"  sleeve {chk['underlying']}: {chk['shares']} sh @ "
              f"${chk['entry_price']:.2f}"
              + (f", now ${chk['spot']:.2f} "
                 f"({chk['unrealized']:+,.0f})" if chk.get("spot") else "")
              + f" -- {chk['reason']}")
    if "decision" in sl:
        print(f"  sleeve {sl.get('underlying')}: {sl['decision']} -- "
              f"{sl.get('reason')}")
        for g in sl.get("gates", []):
            if not _passed_sleeve(g):
                print(f"      VETO: {g}")


def _passed_sleeve(reason: str) -> bool:
    r"""The sleeve's gates answer "S<n> ok", not "G<n> ok". Testing them with
    the options predicate silently reports every S-gate as a veto."""
    return re.match(r"^S\d+ ok\b", reason) is not None


def _reconcile_sleeve(broker, pos, journal, now_et=None):
    """Reconcile the sleeve against the broker, which is the authority.

    Two directions, and the file can be wrong in both.

    *The position is gone.* The whole point of a GTC stop at the broker is
    that it works overnight, which means the position can be closed before
    any cycle looks. Trusting the saved file would then have the agent
    managing -- and on Thursday, trying to SELL -- shares it no longer owns.

    *The position is there and the file does not know it.* A fill is a fact
    at the broker the instant it happens; the file only catches up when the
    cycle finishes writing. Anything in between -- the 25-minute job timeout,
    a runner killed mid-cycle, an exception raised after the fill -- leaves
    shares nobody is managing and no stop resting under them. Worse, the
    entry gate would see a flat sleeve and buy a SECOND position, spending a
    carve-out that funds exactly one stop loss twice and putting the two
    books back through the capital floor. So an unexplained holding in the
    sleeve's underlying is adopted rather than ignored.
    """
    try:
        held = {str(p.get("symbol")): p for p in broker.positions()
                if str(p.get("asset_class", "us_equity")) == "us_equity"}
    except Exception as exc:                                 # noqa: BLE001
        # Unknown is not "gone". Keep managing what we wrote down.
        print(f"[sleeve] could not read broker positions: {exc}",
              file=sys.stderr)
        return pos
    if pos is None:
        return _adopt_orphan_sleeve(held, journal, now_et)
    qty = int(float((held.get(pos.underlying) or {}).get("qty") or 0))
    if qty <= 0:
        print(f"[sleeve] {pos.underlying} is no longer at the broker -- the "
              f"resting stop fired while the agent was down", file=sys.stderr)
        journal.append({"event": "sleeve_stopped_out",
                        "underlying": pos.underlying,
                        "shares": pos.shares,
                        "stop_price": pos.stop_price,
                        "stop_order_id": pos.stop_order_id,
                        "reason": "broker holds none; the GTC stop filled "
                                  "outside a cycle"})
        P.save_sleeve(None, {"underlying": pos.underlying,
                             "decision": "STOPPED_OUT",
                             "reason": "the resting stop filled outside a cycle"},
                      retired=C.SLEEVE_ONE_SHOT)
        return None
    if qty != pos.shares:
        journal.append({"event": "sleeve_share_discrepancy",
                        "underlying": pos.underlying, "tracked": pos.shares,
                        "at_broker": qty,
                        "reason": "managing the broker's count, not ours"})
        return replace(pos, shares=qty)
    return pos


def _adopt_orphan_sleeve(held, journal, now_et):
    """Shares in the sleeve's underlying that no saved state accounts for.

    Priced off the broker's own average entry, so the 4% stop is the stop the
    risk budget was actually derived from. `stop_order_id` is deliberately
    None: that is the flag the cycle's retry looks for, so adopting a position
    also schedules its protection.
    """
    row = held.get(C.SLEEVE_UNDERLYING)
    qty = int(float((row or {}).get("qty") or 0))
    if qty <= 0:
        return None
    entry = float((row or {}).get("avg_entry_price") or 0.0)
    if entry <= 0:
        # A stop priced off nothing is not a stop. Loud, and left alone.
        print(f"[sleeve] WARNING: {qty} {C.SLEEVE_UNDERLYING} at the broker "
              f"with no usable entry price; NOT adopted", file=sys.stderr)
        journal.append({"event": "sleeve_orphan_unpriced",
                        "underlying": C.SLEEVE_UNDERLYING, "shares": qty,
                        "reason": "broker reports no average entry price; a "
                                  "stop cannot be derived, so this needs a "
                                  "human"})
        return None
    stop = round(entry * (1.0 - C.SLEEVE_STOP_PCT), 2)
    print(f"[sleeve] ADOPTED {qty} orphaned {C.SLEEVE_UNDERLYING} @ "
          f"${entry:.2f} -- stop ${stop:.2f} to be placed this cycle",
          file=sys.stderr)
    journal.append({"event": "sleeve_orphan_adopted",
                    "underlying": C.SLEEVE_UNDERLYING, "shares": qty,
                    "entry_price": entry, "stop_price": stop,
                    "reason": "shares at the broker with no saved position: a "
                              "fill that outlived the cycle that made it. "
                              "Adopted so it is managed and not re-bought"})
    return SleevePosition(
        underlying=C.SLEEVE_UNDERLYING, shares=qty, entry_price=entry,
        stop_price=stop, opened_at=now_et or datetime.now(C.ET),
        order_id="", stop_order_id=None)


def _run_replay(fx: Replay) -> int:
    """Deterministic by construction: dry, degraded brain, frozen clock.

    Journal and state go to their own subdirectories so a replay can never be
    mistaken for -- or appended onto -- the live record.
    """
    now_et = fx.as_of_et
    stem = fx.path.stem if fx.path else "fixture"
    # Deliberately outside journal/ and state/: those two directories are what
    # the agent publishes as its audit trail, and a rehearsal must never be
    # able to land in it.
    root = Path("replay_out")
    out = root / "journal" / f"{stem}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)          # a fresh chain, not an appended one
    state.ROOT = root / "state"

    print(f"[replay] {fx.path}  captured {fx.data['captured_utc']}")
    print(f"[replay] clock frozen at {now_et:%Y-%m-%d %H:%M} ET; "
          f"dry, degraded brain -- same fixture, same decisions, every run")

    journal = Journal(out)
    try:
        # cycle 0, deliberately: the ordinal comes from the last heartbeat,
        # and a replay that counted up would put a different number in the
        # chain on every run of the same fixture.
        res = run_cycle(ds=fx, broker=ReplayBroker(), now_et=now_et,
                        market_open=True, journal=journal, dry=True,
                        mind=Mind(api_key=""), cycle=0)
    except ReplayError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 2

    print(f"\nmode={res.mode}  ({res.reason})")
    # The sizer owns 100% of position size, so a rehearsal that never shows it
    # can print twelve green gates while every name sits silently at half
    # weight because the fixture predates the lookback. Read it back from the
    # journal just written -- what is shown is what was actually recorded.
    for rec in Journal(out).read():
        if rec.payload.get("event") == "regime":
            p = rec.payload
            print(f"  {p['underlying']}: regime weight {p['weight']} "
                  f"[{p['source']}] stage2={p['stage2']} "
                  f"ribbon={p['ribbon_bull']} lrs={p['lrs_weight']}"
                  + (f"  -- {p['notes']}" if p["source"] != "measured" else ""))
    # The regime is not the only floor. A replay runs `Mind(api_key="")`, so
    # the ABSENT-brain tier halves the book on top of whatever the trend says
    # -- and printing "regime weight 1.0" beside a one-contract condor with a
    # $1,250 per-position cap leaves a reader no way to reconcile the two.
    for rec in Journal(out).read():
        if rec.payload.get("event") == "mind":
            bf = rec.payload.get("brain_floor", 1.0)
            if bf < 1.0:
                print(f"  brain: no provider configured -- absent-brain floor "
                      f"{bf} applies on top; sizing NAV x min(weight, {bf})")
            break
    for m in res.measurements:
        print(f"  {m['underlying']}: spot {m['spot']}  atm_iv {m['atm_iv']:.1f}  "
              f"rv10 {m['rv10']:.1f}  vrp {m['vrp_ratio']:.2f}  "
              f"skew {m['skew25']:+.2f} (z {m['skew_z']:+.2f})")
    for d in res.decisions:
        print(f"  {d['underlying']}: {d['decision']} -- {d['reason']}")
        # Every gate, pass and fail. A rehearsal that shows only the refusals
        # proves the agent stopped, not that it checked -- and README and
        # WRITEUP both promise the whole evaluation.
        for g in d.get("gates", []):
            print(f"      [{'ok  ' if _passed(g) else 'VETO'}] {g}")
    # The sleeve is a SECOND book on the same account with its own seven
    # gates. A rehearsal that printed twelve green G-gates and said nothing
    # about S1-S7 would be showing a judge half the risk surface.
    sl = res.sleeve
    if sl:
        print(f"\n  sleeve {sl.get('underlying')}: "
              f"{sl.get('decision', 'no decision')} -- {sl.get('reason')}")
        for g in sl.get("gates", []):
            print(f"      [{'ok  ' if _passed_sleeve(g) else 'VETO'}] {g}")

    ok, msg = Journal(out).verify()
    print(f"\n[replay] {out}: {msg}")
    return 0 if ok else 1


def _held_symbols(broker) -> set[str]:
    """Option legs the broker actually holds. Never fatal: a failed position
    read must not stop a cycle that could otherwise manage exits."""
    try:
        return {str(p.get("symbol")) for p in broker.positions()
                if str(p.get("symbol", "")).startswith(("SPY2", "QQQ2", "IWM2"))}
    except Exception as exc:                                 # noqa: BLE001
        print(f"[book] could not read broker positions: {exc}", file=sys.stderr)
        return set()


def _market_open(key: str, sec: str) -> bool:
    from alpaca.trading.client import TradingClient
    return bool(TradingClient(key, sec, paper=True).get_clock().is_open)


if __name__ == "__main__":
    raise SystemExit(main())

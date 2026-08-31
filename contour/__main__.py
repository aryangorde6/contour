"""python -m contour [--once] [--dry] [--as-of ISO] [--verify] [--dev]
                    [--record PATH] [--replay [PATH]]"""
from __future__ import annotations

import argparse
import os
import sys
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
from .replay import Recorder, Replay, ReplayBroker, ReplayError


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
                  "\n  GEMINI_API_KEY, ANTHROPIC_API_KEY")
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
    held = _held_symbols(broker)
    tracked = {l.symbol for p in open_positions for l in p.candidate.legs}
    print(f"[book] {len(open_positions)} tracked position(s), "
          f"{len(held)} option leg(s) at the broker")
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
    res = run_cycle(ds=rec or ds, broker=broker, now_et=now_et,
                    market_open=market_open, journal=journal, dry=args.dry,
                    mind=mind, open_positions=open_positions)
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
            if not g.startswith(("G1 ok", "G2 ok", "G3 ok", "G4 ok", "G5 ok",
                                 "G6 ok", "G7 ok", "G8 ok", "G9 ok", "G10 ok",
                                 "G11 ok", "G12 ok")):
                print(f"      VETO: {g}")
    return 0


def _run_replay(fx: Replay) -> int:
    """Deterministic by construction: dry, degraded brain, frozen clock.

    Journal and state go to their own subdirectories so a replay can never be
    mistaken for -- or appended onto -- the live record.
    """
    from . import state

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
        res = run_cycle(ds=fx, broker=ReplayBroker(), now_et=now_et,
                        market_open=True, journal=journal, dry=True,
                        mind=Mind(api_key=""))
    except ReplayError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 2

    print(f"\nmode={res.mode}  ({res.reason})")
    for m in res.measurements:
        print(f"  {m['underlying']}: spot {m['spot']}  atm_iv {m['atm_iv']:.1f}  "
              f"rv10 {m['rv10']:.1f}  vrp {m['vrp_ratio']:.2f}  "
              f"skew {m['skew25']:+.2f} (z {m['skew_z']:+.2f})")
    for d in res.decisions:
        print(f"  {d['underlying']}: {d['decision']} -- {d['reason']}")
        for g in d.get("gates", []):
            if " ok" not in g.split(":")[0]:
                print(f"      VETO: {g}")

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

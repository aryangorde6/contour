"""python -m contour [--once] [--dry] [--as-of ISO] [--verify] [--dev]"""
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
    res = run_cycle(ds=AlpacaData(key, sec), broker=broker, now_et=now_et,
                    market_open=market_open, journal=journal, dry=args.dry,
                    mind=mind)

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


def _market_open(key: str, sec: str) -> bool:
    from alpaca.trading.client import TradingClient
    return bool(TradingClient(key, sec, paper=True).get_clock().is_open)


if __name__ == "__main__":
    raise SystemExit(main())

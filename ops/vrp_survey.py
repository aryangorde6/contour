"""Read-only VRP survey across optionable ETFs. Places nothing, writes nothing.

    .venv/bin/python ops/vrp_survey.py

Run it when the book is pinned because too few names clear `VRP_RATIO_FLOOR`.
The cheap question to ask before loosening the floor is whether the premium is
simply somewhere else this week -- and on 2026-09-01 the answer was that the
two names which did clear it, EFA and XLE, carry 25-delta premium of $0.35 and
$0.43. G5 caps round-trip friction at 30% of credit; a penny-wide spread on a
$0.35 leg is 6% per leg, four legs each way. Untradeable, not unattractive.

Skew is reported RAW. `skew_z` needs a per-underlying `SKEW_PRIOR` these names
do not have, and inventing one is guessing at a distribution we have not
observed. That absence is part of the finding, not an obstacle to it.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

env = ROOT / ".env"
if env.exists():
    for line in env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v)

from contour import config as C, structures as ST, surface as SF   # noqa: E402
from contour.data import AlpacaData                                # noqa: E402

CANDIDATES = ("SPY", "QQQ", "IWM", "DIA", "EFA", "EEM", "GLD", "SLV",
              "TLT", "XLE", "XLF", "XLK", "XLU", "SMH", "USO", "FXI")


def main() -> int:
    key, sec = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")
    if not (key and sec):
        print("ALPACA_API_KEY / ALPACA_SECRET_KEY not set", file=sys.stderr)
        return 2
    ds = AlpacaData(key, sec)

    print(f"expiry {C.EXPIRY}, floor {C.VRP_RATIO_FLOOR}\n")
    print(f"{'name':6} {'spot':>8} {'IV':>6} {'RV10':>6} {'VRP':>6} {'skew25':>7} "
          f"{'legs':>5} {'25d $':>6}  verdict")
    print("-" * 74)

    paid = []
    for u in CANDIDATES:
        try:
            # Anchored on the last close, not the NBBO: the free feed serves no
            # overnight quote for the thinner names and the survey should still
            # be runnable before the open.
            closes = ds.closes(u, 11)
            legs = ds.legs(u, C.EXPIRY, closes[-1])
            wi = [l for l in legs if l.implied_volatility is not None]
            if not wi:
                print(f"{u:6} {closes[-1]:8.2f}  no IV on any leg")
                continue
            p25 = ST.pick_by_delta([l for l in wi if l.option_type == "put"],
                                   0.25, (0.18, 0.32))
            c25 = ST.pick_by_delta([l for l in wi if l.option_type == "call"],
                                   0.25, (0.18, 0.32))
            if not (p25 and c25):
                print(f"{u:6} {closes[-1]:8.2f}  no 25-delta pair inside the band")
                continue
            iv = SF.atm_iv([(l.strike, l.implied_volatility) for l in wi], closes[-1])
            rv = SF.realized_vol(closes)
            vrp = iv / max(rv, C.RV_FLOOR)
            sk = SF.skew_25(p25.implied_volatility, c25.implied_volatility)
            prem = (p25.bid + p25.ask) / 2.0
            ok = vrp >= C.VRP_RATIO_FLOOR
            if ok:
                paid.append((vrp, u, prem))
            print(f"{u:6} {closes[-1]:8.2f} {iv:6.2f} {rv:6.2f} {vrp:6.3f} "
                  f"{sk:7.2f} {len(wi):5} {prem:6.2f}  "
                  f"{'PAID' if ok else 'below floor'}")
        except Exception as exc:                                  # noqa: BLE001
            print(f"{u:6} {'--':>8}  {type(exc).__name__}: {str(exc)[:40]}")

    print()
    if paid:
        print("clears the floor: " + ", ".join(
            f"{u} {v:.2f} (25d ${p:.2f})" for v, u, p in sorted(paid, reverse=True)))
    else:
        print("nothing clears the floor")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

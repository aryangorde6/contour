"""One-off: rebuild the open book from the broker's own filled orders.

Two SPY condors filled on 2026-08-31 (10:09 and 10:39 ET) before `loop.py`
persisted anything, so `state/positions.json` does not know about them and no
cycle can exit-check them -- including Thursday's scheduled flatten.

This reconstructs them from the orders Alpaca actually filled, not from our
intentions, and writes the book the running agent will pick up. Run once, then
publish `state/` to the agent-state branch.

    python ops/repair_book.py            # show what it would write
    python ops/repair_book.py --write
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from dotenv import load_dotenv

from contour import config as C
from contour import positions as P
from contour.manage import ManagedPosition
from contour.models import Candidate, Leg

API = "https://paper-api.alpaca.markets/v2"


def occ(symbol: str) -> tuple[str, str, float]:
    """SPY260911P00745000 -> ('SPY', 'put', 745.0)."""
    i = next(n for n, ch in enumerate(symbol) if ch.isdigit())
    root, rest = symbol[:i], symbol[i:]
    kind = "put" if rest[6] == "P" else "call"
    return root, kind, int(rest[7:]) / 1000.0


def leg_from(l: dict) -> Leg:
    root, kind, strike = occ(l["symbol"])
    px = float(l["filled_avg_price"])
    return Leg(
        symbol=l["symbol"],
        side="sell" if l["side"].startswith("sell") else "buy",
        ratio_qty=int(l.get("ratio_qty") or 1),
        option_type=kind, strike=strike, expiration_date=C.EXPIRY,
        # The fill price is the only price we can honestly claim for a leg we
        # are reconstructing after the fact; it is used for the wing geometry
        # and the breach test, both of which key off strike, not quote.
        bid=px, ask=px, delta=None, implied_volatility=None,
        open_interest=0, tradable=True, close_price=px, quote_age_s=None,
    )


def rebuild(order: dict) -> ManagedPosition:
    legs = [leg_from(l) for l in order["legs"] if l.get("filled_avg_price")]
    root = occ(legs[0].symbol)[0]
    credit = sum(l.bid if l.side == "sell" else -l.bid for l in legs)
    wing = C.WING_WIDTH[root]
    filled = int(float(order["filled_qty"]))
    cand = Candidate(
        underlying=root, structure="CONDOR", legs=tuple(legs),
        net_credit=abs(credit), wing_width=wing, contracts=filled,
        max_loss_per_contract=(wing - abs(credit)) * 100.0,
    )
    return ManagedPosition(
        candidate=cand, credit_received=abs(credit),
        opened_at=datetime.fromisoformat(
            order["filled_at"].replace("Z", "+00:00")).astimezone(C.ET),
        order_id=order["id"],
    )


def main() -> int:
    load_dotenv()
    k, s = os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY")
    if not k or not s:
        print("missing ALPACA_API_KEY / ALPACA_SECRET_KEY", file=sys.stderr)
        return 2
    r = httpx.get(f"{API}/orders", params={"status": "all", "limit": 100,
                                           "nested": "true"},
                  headers={"APCA-API-KEY-ID": k, "APCA-API-SECRET-KEY": s},
                  timeout=30)
    r.raise_for_status()
    orders = [o for o in r.json()
              if o.get("status") == "filled" and o.get("legs")
              and int(float(o.get("filled_qty") or 0)) > 0]

    book = [rebuild(o) for o in orders]
    for p in book:
        print(f"{p.order_id[:8]}  {p.candidate.underlying} "
              f"{p.candidate.structure} x{p.candidate.contracts}  "
              f"credit ${p.credit_received:.2f}  "
              f"max loss ${p.candidate.max_loss_per_contract:.0f}/ct  "
              f"opened {p.opened_at:%H:%M ET}")
        for l in p.candidate.legs:
            print(f"     {l.side:4} {l.symbol} @ {l.bid}")

    if "--write" not in sys.argv:
        print("\ndry run -- pass --write to save state/positions.json")
        return 0
    path = P.save(book)
    print(f"\nwrote {path} with {len(book)} position(s)")
    reread = P.load()
    assert len(reread) == len(book), "book did not round-trip"
    print(f"verified: {len(reread)} position(s) load back cleanly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

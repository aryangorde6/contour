"""Exits. Written before go-live, deliberately.

Alpaca supports no stop or stop_limit order on a multi-leg position -- those
are single-leg only. There is therefore NO resting protective order: the exit
is a polling loop, and if this module does not exist before the first entry,
the book is unmanaged on a scored account.

Three ways out on merit, one on the clock:
  1. profit target  buy back at <= 50% of the credit received
  2. stop           mark reaches 2.0x credit (loss = 1.0x credit)
  3. breach         spot through a short strike by > 0.30 x wing width
  4. flatten        Thursday 15:45 ET, scheduled, not a heuristic
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Sequence

from . import config as C
from . import structures as S
from .models import Candidate, Leg

# Alpaca returns this when a close would leave an uncovered short leg.
UNCOVERED_CODE = 40310000


@dataclass(frozen=True)
class ManagedPosition:
    candidate: Candidate
    credit_received: float          # per contract, actual fill not mid
    opened_at: datetime
    order_id: str


def short_strikes(cand: Candidate) -> list[Leg]:
    return [l for l in cand.legs if l.is_short]


def should_exit(pos: ManagedPosition, mark: float, spot: float,
                now_et: datetime) -> tuple[bool, str]:
    """Pure. `mark` is the current cost to buy the package back, per contract,
    as a positive number."""
    cand = pos.candidate
    credit = pos.credit_received

    # 4. the clock beats every other consideration
    if now_et.date() > C.FLATTEN_DAY or (
        now_et.date() == C.FLATTEN_DAY and now_et.time() >= C.FLATTEN_AT
    ):
        return True, (f"FLATTEN: scheduled hard flatten at "
                      f"{C.FLATTEN_DAY} {C.FLATTEN_AT:%H:%M} ET")

    # 1. profit target
    if mark <= C.PROFIT_TARGET_PCT_OF_CREDIT * credit:
        return True, (f"PROFIT_TARGET: mark ${mark:.2f} <= "
                      f"{C.PROFIT_TARGET_PCT_OF_CREDIT:.0%} of ${credit:.2f} credit")

    # 2. stop
    if mark >= C.STOP_MULTIPLE_OF_CREDIT * credit:
        return True, (f"STOP: mark ${mark:.2f} >= "
                      f"{C.STOP_MULTIPLE_OF_CREDIT:.1f}x ${credit:.2f} credit")

    # 3. breach -- regardless of P&L
    limit = C.BREACH_FRACTION_OF_WING * cand.wing_width
    for leg in short_strikes(cand):
        if leg.option_type == "put" and spot < leg.strike - limit:
            return True, (f"BREACH: spot {spot:.2f} is {leg.strike - spot:.2f} "
                          f"below short put {leg.strike:.0f} (limit {limit:.2f})")
        if leg.option_type == "call" and spot > leg.strike + limit:
            return True, (f"BREACH: spot {spot:.2f} is {spot - leg.strike:.2f} "
                          f"above short call {leg.strike:.0f} (limit {limit:.2f})")

    return False, (f"HOLD: mark ${mark:.2f} vs credit ${credit:.2f}, "
                   f"spot {spot:.2f}")


def legout_order(cand: Candidate) -> list[list[dict]]:
    """Fallback when a single reversed-intent mleg close is refused.

    SHORTS ARE BOUGHT BACK FIRST, always. Closing a long leg first would leave
    a naked short in the account for as long as the second order takes to fill,
    which is both the 40310000 rejection and a genuinely unbounded risk.
    """
    shorts = [l for l in cand.legs if l.is_short]
    longs = [l for l in cand.legs if not l.is_short]
    batches = []
    for leg in shorts + longs:
        side = "buy" if leg.is_short else "sell"
        intent = "buy_to_close" if leg.is_short else "sell_to_close"
        batches.append([{"symbol": leg.symbol, "ratio_qty": str(leg.ratio_qty),
                         "side": side, "position_intent": intent}])
    return batches


def close_ladder_prices(mark: float, escalate: bool) -> list[float]:
    """Closing is a DEBIT, so every price is positive. Escalation exists
    because banning market orders, having no resting stops, and holding a hard
    flatten deadline is otherwise a trap with no exit."""
    rungs = [1.00, 1.05, 1.10]
    if escalate:
        rungs += list(C.CLOSE_ESCALATION_RUNGS)
    return [S.net_limit_price(r * mark, "debit") for r in rungs]


def is_uncovered_rejection(err: Any) -> bool:
    text = str(err)
    return str(UNCOVERED_CODE) in text or "uncovered option" in text.lower()


def close_position(broker, pos: ManagedPosition, mark: float, base_id: str,
                   journal: Callable[[dict], Any], escalate: bool = False,
                   submit=None) -> dict:
    """Try the clean single-order close first, then leg out shorts-first."""
    submit = submit or broker.submit_mleg
    cand = pos.candidate
    legs = S.to_cli_legs(cand, closing=True)

    for i, price in enumerate(close_ladder_prices(mark, escalate), start=1):
        coid = f"{base_id}-c{i}"
        try:
            order = submit(legs, cand.contracts, price, coid)
        except Exception as exc:                       # noqa: BLE001
            if is_uncovered_rejection(exc):
                journal({"event": "close_rejected_uncovered", "rung": i,
                         "error": str(exc),
                         "action": "falling back to shorts-first legout"})
                return _legout(broker, pos, mark, base_id, journal, submit)
            journal({"event": "close_error", "rung": i, "error": str(exc)})
            continue
        journal({"event": "close_submitted", "rung": i, "order_id": order.get("id"),
                 "limit_price": price, "status": order.get("status")})
        if order.get("status") in {"filled", "accepted", "new", "pending_new"}:
            return {"closed": True, "order_id": order.get("id"), "rung": i}

    journal({"event": "close_failed", "base_id": base_id,
             "reason": "every closing rung failed"})
    return {"closed": False, "order_id": None, "rung": None}


def _legout(broker, pos: ManagedPosition, mark: float, base_id: str,
            journal: Callable[[dict], Any], submit) -> dict:
    ids = []
    for n, batch in enumerate(legout_order(pos.candidate), start=1):
        coid = f"{base_id}-L{n}"
        order = submit(batch, pos.candidate.contracts,
                       S.net_limit_price(1.10 * mark, "debit"), coid)
        ids.append(order.get("id"))
        journal({"event": "legout", "step": n, "symbol": batch[0]["symbol"],
                 "side": batch[0]["side"], "order_id": order.get("id")})
    return {"closed": True, "order_id": ids, "rung": "legout"}

"""Exit triggers and the shorts-first legout ordering."""
from __future__ import annotations

from datetime import datetime

import pytest

from contour import config as C
from contour import manage as M
from contour import structures as S

from .test_gates import leg

ET = C.ET


def condor(nav=100_000):
    return S.build("SPY", "CONDOR", [
        leg(side="sell", strike=749.0, delta=-0.13, bid=1.27, ask=1.32, otype="put"),
        leg(side="buy", strike=744.0, delta=-0.06, bid=0.95, ask=0.96, otype="put"),
        leg(side="sell", strike=785.0, delta=0.13, bid=0.85, ask=0.90, otype="call"),
        leg(side="buy", strike=790.0, delta=0.06, bid=0.34, ask=0.35, otype="call"),
    ], nav=nav)


def pos(credit=0.87):
    return M.ManagedPosition(candidate=condor(), credit_received=credit,
                             opened_at=datetime(2026, 8, 31, 10, 5, tzinfo=ET),
                             order_id="o1")


MID_SESSION = datetime(2026, 9, 1, 12, 0, tzinfo=ET)


def test_profit_target_closes_at_half_the_credit():
    out, why = M.should_exit(pos(), mark=0.43, spot=769.0, now_et=MID_SESSION)
    assert out and "PROFIT_TARGET" in why


def test_stop_closes_at_two_times_credit():
    out, why = M.should_exit(pos(), mark=1.75, spot=769.0, now_et=MID_SESSION)
    assert out and "STOP" in why


def test_breach_closes_regardless_of_pnl():
    """Spot through the short put by more than 0.30 x $5 wing = $1.50."""
    out, why = M.should_exit(pos(), mark=0.90, spot=747.0, now_et=MID_SESSION)
    assert out and "BREACH" in why and "short put" in why

    out, _ = M.should_exit(pos(), mark=0.90, spot=748.0, now_et=MID_SESSION)
    assert not out, "1.0 below the strike is inside the 1.50 tolerance"

    out, why = M.should_exit(pos(), mark=0.90, spot=787.0, now_et=MID_SESSION)
    assert out and "short call" in why


def test_hold_when_nothing_has_triggered():
    out, why = M.should_exit(pos(), mark=0.80, spot=769.0, now_et=MID_SESSION)
    assert not out and why.startswith("HOLD")


def test_flatten_deadline_overrides_a_winning_position():
    late = datetime(2026, 9, 3, 15, 46, tzinfo=ET)
    out, why = M.should_exit(pos(), mark=0.80, spot=769.0, now_et=late)
    assert out and "FLATTEN" in why

    early = datetime(2026, 9, 3, 15, 44, tzinfo=ET)
    out, _ = M.should_exit(pos(), mark=0.80, spot=769.0, now_et=early)
    assert not out


def test_legout_buys_back_every_short_before_selling_any_long():
    batches = M.legout_order(condor())
    sides = [b[0]["side"] for b in batches]
    assert sides == ["buy", "buy", "sell", "sell"], (
        "shorts must be closed first -- selling a long first leaves a naked "
        f"short in the account, got {sides}")
    intents = [b[0]["position_intent"] for b in batches]
    assert intents[:2] == ["buy_to_close", "buy_to_close"]


def test_closing_prices_are_positive_debits_and_escalate():
    plain = M.close_ladder_prices(1.00, escalate=False)
    assert all(p > 0 for p in plain), "closing pays a debit -- must be positive"
    assert plain == sorted(plain)

    hot = M.close_ladder_prices(1.00, escalate=True)
    assert len(hot) == 5 and hot[-1] == pytest.approx(1.30)


def test_uncovered_rejection_is_detected_by_code_and_by_text():
    assert M.is_uncovered_rejection("submit rc=1: code 40310000")
    assert M.is_uncovered_rejection("account not eligible to trade uncovered option contracts")
    assert not M.is_uncovered_rejection("insufficient buying power")


def test_close_falls_back_to_legout_on_uncovered_rejection():
    calls = []

    def submit(legs, qty, price, coid):
        calls.append((legs, coid))
        if len(calls) == 1:
            raise RuntimeError("submit rc=1: code 40310000 uncovered option")
        return {"id": f"ord{len(calls)}", "status": "accepted"}

    events = []
    out = M.close_position(broker=None, pos=pos(), mark=1.00, base_id="b",
                           journal=events.append, submit=submit)
    assert out["closed"] and out["rung"] == "legout"
    kinds = [e["event"] for e in events]
    assert "close_rejected_uncovered" in kinds
    assert kinds.count("legout") == 4

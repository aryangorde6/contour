"""Six tests on the limit price sign alone.

NEGATIVE = net credit, POSITIVE = net debit. Backwards on a credit spread
either rejects outright or fills terribly, and it is silent either way.
"""
from __future__ import annotations

from datetime import date

import pytest

from contour import config as C
from contour import structures as S
from contour.models import Leg

from .test_gates import leg


# ---- the sign convention: six tests -------------------------------------
def test_credit_is_submitted_negative():
    assert S.net_limit_price(0.87, "credit") == -0.87


def test_debit_is_submitted_positive():
    assert S.net_limit_price(0.87, "debit") == 0.87


def test_sign_round_trips_to_two_decimals():
    assert S.net_limit_price(0.8749, "credit") == -0.87
    assert S.net_limit_price(0.8751, "credit") == -0.88


def test_negative_magnitude_is_rejected_not_silently_flipped():
    """The failure mode this guards: someone passes an already-negative
    credit, it gets negated to positive, and a credit spread is submitted
    as a debit."""
    with pytest.raises(ValueError, match="positive magnitude"):
        S.net_limit_price(-0.87, "credit")


def test_unknown_direction_raises():
    with pytest.raises(ValueError, match="credit.*debit"):
        S.net_limit_price(0.87, "net")          # type: ignore[arg-type]


def test_ladder_rungs_are_all_negative_and_monotonically_worse():
    rungs = S.ladder_prices(0.87)
    assert all(p < 0 for p in rungs), "every credit rung must be negative"
    # Less negative = accepting less credit = worse for us, in order.
    assert rungs == sorted(rungs), f"rungs must degrade monotonically: {rungs}"
    assert rungs[0] == pytest.approx(-0.84, abs=0.01)
    assert rungs[-1] == pytest.approx(-0.77, abs=0.01)


# ---- mleg structural constraints ----------------------------------------
def test_validate_legs_enforces_max_four_and_gcd_one():
    four = [leg(), leg(strike=744.0), leg(strike=785.0), leg(strike=790.0)]
    ok, why = S.validate_legs(four)
    assert ok, why

    five = four + [leg(strike=795.0)]
    ok, why = S.validate_legs(five)
    assert not ok and "4-leg" in why

    bad_ratio = [
        Leg(**{**vars(leg()), "ratio_qty": 2}),
        Leg(**{**vars(leg(strike=744.0)), "ratio_qty": 4}),
    ]
    ok, why = S.validate_legs(bad_ratio)
    assert not ok and "GCD" in why

    mixed_exp = [leg(), Leg(**{**vars(leg(strike=744.0)),
                              "expiration_date": date(2026, 9, 4)})]
    ok, why = S.validate_legs(mixed_exp)
    assert not ok and "one expiration" in why


def test_net_credit_shorts_add_longs_subtract():
    """The live SPY Sep-11 condor: 1.295 - 0.955 + 0.875 - 0.345 = 0.870"""
    legs = [
        leg(side="sell", strike=749.0, bid=1.27, ask=1.32),
        leg(side="buy", strike=744.0, bid=0.95, ask=0.96),
        leg(side="sell", strike=785.0, bid=0.85, ask=0.90),
        leg(side="buy", strike=790.0, bid=0.34, ask=0.35),
    ]
    assert S.net_credit_from_mids(legs) == pytest.approx(0.870, abs=0.001)


def test_contracts_sizing_respects_the_per_position_cap():
    cap = C.MAX_POSITION_RISK_PCT * 100_000                       # $1,250
    # $410 max loss against that cap -> 3 contracts
    assert S.contracts_for(100_000, 410.0) == 3
    # a single contract over the cap means skip the name entirely
    assert S.contracts_for(100_000, cap + 1) == 0
    assert S.contracts_for(100_000, cap) == 1


def test_pick_by_delta_never_coerces_a_null():
    cands = [leg(delta=None), leg(strike=750.0, delta=-0.13),
             leg(strike=760.0, delta=-0.30)]
    got = S.pick_by_delta(cands, 0.13, C.SHORT_DELTA_BAND)
    assert got is not None and got.strike == 750.0

    only_null = [leg(delta=None)]
    assert S.pick_by_delta(only_null, 0.13, C.SHORT_DELTA_BAND) is None


def test_closing_payload_reverses_every_intent():
    cand = S.build("SPY", "CONDOR", [
        leg(side="sell", strike=749.0, bid=1.27, ask=1.32),
        leg(side="buy", strike=744.0, bid=0.95, ask=0.96),
        leg(side="sell", strike=785.0, bid=0.85, ask=0.90),
        leg(side="buy", strike=790.0, bid=0.34, ask=0.35),
    ], nav=100_000)
    assert cand is not None
    opening = S.to_cli_legs(cand, closing=False)
    closing = S.to_cli_legs(cand, closing=True)
    assert [l["position_intent"] for l in opening] == [
        "sell_to_open", "buy_to_open", "sell_to_open", "buy_to_open"]
    assert [l["position_intent"] for l in closing] == [
        "buy_to_close", "sell_to_close", "buy_to_close", "sell_to_close"]
    assert [l["side"] for l in closing] == ["buy", "sell", "buy", "sell"]


def test_build_matches_the_live_measured_condor():
    """End to end against the real 2026-08-30 SPY Sep-11 chain."""
    cand = S.build("SPY", "CONDOR", [
        leg(side="sell", strike=749.0, bid=1.27, ask=1.32),
        leg(side="buy", strike=744.0, bid=0.95, ask=0.96),
        leg(side="sell", strike=785.0, bid=0.85, ask=0.90),
        leg(side="buy", strike=790.0, bid=0.34, ask=0.35),
    ], nav=100_000)
    assert cand is not None
    assert cand.net_credit == pytest.approx(0.870, abs=0.001)
    assert cand.max_loss_per_contract == pytest.approx(413.0, abs=0.5)
    assert cand.contracts == 3, (
        f"{C.MAX_POSITION_RISK_PCT:.2%} of $100k / $413 = 3 contracts")

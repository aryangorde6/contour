"""The volume-profile strike filter: a measured distribution against a modelled one.

The claim under test is narrow and it is worth stating precisely, because the
module is easy to over-read. The profile does NOT predict direction, size
anything, or choose a structure. It can do exactly one thing: remove a short
CALL strike that the delta band already accepted, on the evidence that strikes
inside the traded value area are touched materially more often than strikes
the same distance away but outside it.

So these tests care about three properties. The value area has to actually
find where volume sat. The filter has to veto only upward, never the put side.
And every unreadable input -- no bars, no volume, no method on the seam -- has
to degrade to "vetoes nothing" rather than to a guess or an exception, because
this filter sits in front of the only structure that pays the book.
"""
from __future__ import annotations

from datetime import date

import pytest

from contour import config as C
from contour import profile as VP
from contour import structures as S
from contour.models import Bar, Leg


def bar(high, low, volume=1_000_000.0):
    return Bar(high=high, low=low, close=(high + low) / 2.0, volume=volume)


def flat_window(n=20, high=101.0, low=99.0, volume=1_000_000.0):
    return [bar(high, low, volume) for _ in range(n)]


def leg(symbol, option_type, strike, delta, bid=1.00, ask=1.10):
    return Leg(symbol=symbol, side="buy", ratio_qty=1, option_type=option_type,
               strike=strike, expiration_date=date(2026, 9, 11), bid=bid,
               ask=ask, delta=delta, implied_volatility=0.15,
               open_interest=5_000, tradable=True, close_price=1.05,
               quote_age_s=1.0)


# --- the measurement itself ------------------------------------------------

def test_the_poc_lands_where_the_volume_actually_sat():
    """Nineteen quiet bars around 100 and one wide bar that traded nowhere
    near them. The POC must follow the volume, not the range."""
    bars = flat_window(19, high=100.5, low=99.5, volume=1_000_000.0)
    bars.append(bar(high=130.0, low=120.0, volume=1.0))
    p = VP.value_area("SPY", bars)
    assert p.source == "measured"
    assert 99.0 <= p.poc <= 101.0
    # and the value area must not be dragged up to the outlier
    assert p.vah < 110.0


def test_the_value_area_brackets_the_poc_and_stops_short_of_the_full_range():
    bars = flat_window(10, high=100.2, low=99.8)
    bars += [bar(105.0, 95.0, volume=10_000.0) for _ in range(10)]
    p = VP.value_area("SPY", bars)
    assert p.val <= p.poc <= p.vah
    assert p.val > 95.0 and p.vah < 105.0


def test_volume_is_spread_across_a_bar_rather_than_dumped_at_one_price():
    """A single wide bar has no single busiest price, so its value area must
    span most of its range instead of collapsing onto one bin."""
    p = VP.value_area("SPY", [bar(110.0, 90.0)] * 12)
    assert p.vah - p.val > 10.0


# --- degradation, which is the whole safety story --------------------------

def test_too_few_bars_degrades_instead_of_measuring_noise():
    p = VP.value_area("SPY", flat_window(C.PROFILE_MIN_BARS - 1))
    assert p.source == "degraded"
    assert "bars" in p.notes


def test_a_window_with_no_volume_degrades_rather_than_dividing_by_zero():
    p = VP.value_area("SPY", flat_window(volume=0.0))
    assert p.source == "degraded"


def test_a_window_with_no_range_degrades_rather_than_dividing_by_zero():
    p = VP.value_area("SPY", [bar(100.0, 100.0) for _ in range(20)])
    assert p.source == "degraded"


def test_a_bar_that_never_moved_is_binned_rather_than_crashing():
    bars = flat_window(19)
    bars.append(bar(100.0, 100.0))
    assert VP.value_area("SPY", bars).source == "measured"


# --- the filter ------------------------------------------------------------

def test_a_call_strike_clear_of_the_value_area_is_allowed():
    p = VP.value_area("SPY", flat_window())
    assert VP.call_strike_ok(p.vah + 1.0, p)


def test_a_call_strike_inside_the_value_area_is_refused():
    p = VP.value_area("SPY", flat_window())
    assert not VP.call_strike_ok(p.vah - 1.0, p)


def test_a_call_strike_exactly_at_the_upper_edge_is_refused():
    """VAH is the last price the band contains, so a strike sitting on it is
    inside it. The buffer exists to make that unambiguous."""
    p = VP.value_area("SPY", flat_window())
    assert not VP.call_strike_ok(p.vah, p)


def test_a_degraded_profile_vetoes_nothing():
    p = VP.degraded("SPY", "no bars")
    assert VP.call_strike_ok(1.0, p)
    assert VP.call_strike_ok(10_000.0, p)


def test_an_absent_profile_vetoes_nothing():
    assert VP.call_strike_ok(1.0, None)


# --- what assemble does with it -------------------------------------------

def chain(spot=100.0):
    """A chain whose 0.13-delta strikes sit at 106 (call) and 94 (put)."""
    legs = []
    for k, d in ((104.0, 0.22), (106.0, 0.13), (108.0, 0.07), (111.0, 0.03)):
        legs.append(leg(f"C{int(k)}", "call", k, d))
    for k, d in ((96.0, -0.22), (94.0, -0.13), (92.0, -0.07), (89.0, -0.03)):
        legs.append(leg(f"P{int(k)}", "put", k, d))
    return legs


@pytest.fixture
def filter_on(monkeypatch):
    """The filter SHIPS DISABLED -- `research/strategy_backtest.py` measured it
    cutting P&L from +$926 to +$230 across 387 cycles, and config.py carries
    the reasoning. Its mechanics are still pinned here, because a feature kept
    as a documented negative result has to keep working if anyone turns it back
    on. These tests therefore enable it explicitly rather than relying on a
    default that now says the opposite."""
    monkeypatch.setattr(C, "PROFILE_ENABLED", True)


def test_a_condor_is_unchanged_when_the_profile_is_below_the_call_strikes(filter_on):
    p = VP.value_area("SPY", flat_window(high=101.0, low=99.0))
    legs, structure, note = S.assemble("CONDOR", chain(), "SPY", p)
    assert structure == "CONDOR"
    assert len(legs) == 4
    assert note == "assembled as requested"


def test_a_condor_drops_its_call_side_when_the_value_area_covers_it(filter_on):
    """The value area reaches 110, so every in-band call strike is inside the
    traded band. The book must end up short puts only -- and must SAY so."""
    p = VP.value_area("SPY", [bar(112.0, 99.0) for _ in range(20)])
    legs, structure, note = S.assemble("CONDOR", chain(), "SPY", p)
    assert structure == "PUT_CS"
    assert all(l.option_type == "put" for l in legs)
    assert "value area" in note


def test_a_call_spread_becomes_no_trade_when_every_strike_is_inside(filter_on):
    p = VP.value_area("SPY", [bar(112.0, 99.0) for _ in range(20)])
    legs, structure, note = S.assemble("CALL_CS", chain(), "SPY", p)
    assert legs is None
    assert structure == "NO_TRADE"


def test_the_put_side_is_never_filtered_by_the_profile(filter_on):
    """The put-side test returned the wrong sign, so the filter must not
    reach it even when the value area sits far below every put strike."""
    p = VP.value_area("SPY", [bar(101.0, 85.0) for _ in range(20)])
    legs, structure, note = S.assemble("PUT_CS", chain(), "SPY", p)
    assert structure == "PUT_CS"
    assert len(legs) == 2


def test_a_degraded_profile_leaves_the_structure_exactly_as_chosen(filter_on):
    legs, structure, _ = S.assemble("CONDOR", chain(), "SPY",
                                    VP.degraded("SPY", "no bars"))
    assert structure == "CONDOR" and len(legs) == 4


def test_no_profile_at_all_leaves_the_structure_exactly_as_chosen(filter_on):
    legs, structure, _ = S.assemble("CONDOR", chain(), "SPY", None)
    assert structure == "CONDOR" and len(legs) == 4


def test_the_wing_is_still_measured_from_the_short_that_survived_the_filter(filter_on):
    """The filter judges where we are SHORT. The protective wing is chosen by
    strike distance from that short and must not itself be filtered out --
    a vetoed wing would leave the position naked."""
    p = VP.value_area("SPY", [bar(105.5, 99.0) for _ in range(20)])
    legs, structure, note = S.assemble("CALL_CS", chain(), "SPY", p)
    assert structure == "CALL_CS"
    short = [l for l in legs if l.is_short][0]
    long = [l for l in legs if not l.is_short][0]
    assert VP.call_strike_ok(short.strike, p), "short must be clear of the band"
    assert long.strike > short.strike, "the wing must still be above the short"


def test_turning_the_filter_off_restores_the_original_behaviour(monkeypatch):
    """The escape hatch has to be real: with PROFILE_ENABLED false the module
    cannot change a single strike, whatever the profile says."""
    monkeypatch.setattr(C, "PROFILE_ENABLED", False)
    p = VP.value_area("SPY", [bar(112.0, 99.0) for _ in range(20)])
    legs, structure, _ = S.assemble("CONDOR", chain(), "SPY", p)
    assert structure == "CONDOR" and len(legs) == 4


def test_the_filter_is_disabled_by_default_because_it_lost_money():
    """The one test that would have caught this being shipped on a touch-rate
    study alone. `research/strategy_backtest.py` is the evidence; this pins the
    conclusion so nobody flips the default back without re-reading it."""
    assert C.PROFILE_ENABLED is False
    p = VP.value_area("SPY", [bar(112.0, 99.0) for _ in range(20)])
    legs, structure, _ = S.assemble("CONDOR", chain(), "SPY", p)
    assert structure == "CONDOR", "a disabled filter must not touch the book"
    assert len(legs) == 4

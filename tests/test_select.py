"""The structure map's four branches at boundary values."""
from __future__ import annotations

from contour import config as C
from contour.select import choose_structure
from contour.models import Measurement


def m(vrp=1.60, skew_z=0.0, underlying="SPY"):
    return Measurement(underlying=underlying, spot=769.35, atm_iv=14.5,
                       rv10=9.0, vrp_ratio=vrp, skew25=4.5, skew_z=skew_z)


def test_no_trade_when_vol_is_not_rich_enough():
    s, why = choose_structure(m(vrp=1.29))
    assert s == "NO_TRADE" and "VRP_TOO_LOW" in why


def test_rich_put_skew_sells_puts_only():
    s, why = choose_structure(m(skew_z=0.8))
    assert s == "PUT_CS" and "not the cheap calls" in why


def test_rich_call_skew_sells_calls_only():
    s, why = choose_structure(m(skew_z=-0.8))
    assert s == "CALL_CS" and "not the cheap puts" in why


def test_neutral_skew_sells_both_sides():
    s, why = choose_structure(m(skew_z=0.0))
    assert s == "CONDOR" and "SKEW_NEUTRAL" in why


def test_vrp_floor_is_checked_before_skew():
    """A rich skew must not rescue a chain that is not paying enough."""
    s, _ = choose_structure(m(vrp=1.0, skew_z=3.0))
    assert s == "NO_TRADE"


def test_seeded_priors_read_neutral_on_the_day_they_were_measured():
    """Guards the calibration bug: priors 2 vol points too high put every
    underlying at z <= -0.9 and sold calls on all three, all week."""
    from contour.surface import skew_z
    for und, measured in (("SPY", 2.52), ("QQQ", 2.81), ("IWM", 3.10)):
        z = skew_z(und, measured)
        assert abs(z) < 0.8, f"{und} must read neutral at its seed, got z={z:+.2f}"
        s, _ = choose_structure(m(vrp=1.55, skew_z=z, underlying=und))
        assert s == "CONDOR"


def test_a_real_skew_move_still_flips_the_structure():
    """The map must not be inert -- half a standard deviation should not flip
    it, but two should."""
    from contour.surface import skew_z
    assert choose_structure(m(skew_z=skew_z("SPY", 2.52 + 1.2 * 0.5)))[0] == "CONDOR"
    assert choose_structure(m(skew_z=skew_z("SPY", 2.52 + 1.2 * 2)))[0] == "PUT_CS"
    assert choose_structure(m(skew_z=skew_z("SPY", 2.52 - 1.2 * 2)))[0] == "CALL_CS"

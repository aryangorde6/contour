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


def test_todays_real_spy_reads_as_condor():
    """Live 2026-08-30: put IV 13.9 vs call IV 9.4 -> skew25 4.5, exactly the
    SPY prior, so skew_z = 0.0 and both sides are fairly priced."""
    from contour.surface import skew_25, skew_z
    sk = skew_25(0.139, 0.094)
    z = skew_z("SPY", sk)
    s, _ = choose_structure(m(vrp=1.55, skew_z=z))
    assert s == "CONDOR"

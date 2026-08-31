"""The regime sizer: three published trend systems in place of an anchored model.

The point of these tests is not that the arithmetic is right -- it is that the
COMBINATION rule holds and that the failure mode is degradation rather than a
guess. The sizer replaced an LLM multiplier that returned 0.5 sixteen times in
a row while telling four different stories about why; a replacement that
silently falls back to a constant would be the same bug with better sources.
"""
from __future__ import annotations

import math
import random
from datetime import date, timedelta

import pytest

from contour import config as C
from contour import regime as R


def rising(n=420, start=100.0, drift=0.0006, vol=0.0, seed=1):
    """A series with a controllable drift and per-bar noise."""
    rnd = random.Random(seed)
    out, px = [], start
    for _ in range(n):
        px *= 1.0 + drift + (rnd.gauss(0, vol) if vol else 0.0)
        out.append(px)
    return out


# --- the three component states -------------------------------------------
def test_a_clean_uptrend_is_stage_2():
    assert R.stage2(rising()) is True


def test_a_downtrend_is_not_stage_2():
    assert R.stage2(rising(drift=-0.0006)) is False


def test_stage_2_needs_the_stage_line_itself_to_be_rising():
    """Above the line is not the same as being in Stage 2.

    Rally, roll over, then bounce back above a stage line that is still
    falling. Weinstein calls this Stage 4 with a rally in it, and the whole
    point of the `rising` term is to refuse it.
    """
    up = rising(300, drift=0.0018)
    down = [up[-1] * (1 - 0.0016 * i) for i in range(1, 121)]
    bounce = [down[-1] * (1 + 0.0035 * i) for i in range(1, 41)]
    s = up + down + bounce
    sma = sum(s[-C.STAGE2_SMA_D:]) / C.STAGE2_SMA_D
    prior = sum(s[:-C.STAGE2_RISING_D][-C.STAGE2_SMA_D:]) / C.STAGE2_SMA_D
    assert s[-1] > sma, "price should be back above the stage line"
    assert sma < prior, "the stage line should still be falling"
    assert R.stage2(s) is False


def test_the_breakout_trigger_is_reported_but_never_sized_on():
    up = rising()
    assert R.at_52w_high(up) is True
    # It appears nowhere in the weight -- only the persistent state does.
    assert R.assess("SPY", up).weight == R.lrs_weight(up)


def test_the_ribbon_needs_the_stack_and_the_trend_anchor():
    assert R.ribbon_bull(rising()) is True
    assert R.ribbon_bull(rising(drift=-0.0006)) is False


# --- LRS-VT2 weight --------------------------------------------------------
def test_a_calm_uptrend_takes_full_weight():
    assert R.lrs_weight(rising()) == 1.0


def test_the_warning_rung_holds_half_weight():
    # Above the slow SMA, below the fast one -- the breakdown zone.
    up = rising(400)
    s = up + [up[-1] * (1 - 0.001 * i) for i in range(1, 26)]
    px, slow, fast = s[-1], sum(s[-200:]) / 200, sum(s[-50:]) / 50
    assert slow < px < fast, "series did not land in the warning rung"
    assert R.lrs_weight(s) == pytest.approx(C.LRS_WARN_W)


def test_below_both_moving_averages_is_zero_weight():
    s = rising(400) + [rising(400)[-1] * (1 - 0.004 * i) for i in range(1, 121)]
    assert R.lrs_weight(s) == 0.0


def test_the_vol_veto_fires_only_when_recent_vol_runs_hot():
    calm = rising(vol=0.002, seed=7)
    assert R.lrs_weight(calm) == 1.0
    # Same drift, a violent last month. Still above both SMAs, so any trim
    # here is the vol scaler and nothing else.
    rnd = random.Random(3)
    hot = calm[:-20] + [calm[-20] * math.prod(
        1.0 + rnd.gauss(0.001, 0.03) for _ in range(i + 1)) for i in range(20)]
    if hot[-1] > sum(hot[-50:]) / 50:            # keep the ladder out of it
        assert R.lrs_weight(hot) < 1.0


def test_the_overextension_trim_caps_a_parabolic_run():
    s = rising(400, drift=0.004)
    ext = s[-1] / (sum(s[-200:]) / 200) - 1.0
    assert ext > C.LRS_EXT_CAP, "series was not parabolic enough to trim"
    assert R.lrs_weight(s) == pytest.approx(C.LRS_EXT_CAP / ext)


# --- the combination rule --------------------------------------------------
def test_both_confirmations_take_the_lrs_weight_whole():
    up = rising()
    r = R.assess("SPY", up)
    assert (r.stage2, r.ribbon_bull, r.source) == (True, True, "measured")
    assert r.weight == r.lrs_weight == 1.0


def test_one_confirmation_halves_the_weight(monkeypatch):
    up = rising()
    monkeypatch.setattr(R, "ribbon_bull", lambda _c: False)
    r = R.assess("SPY", up)
    assert (r.stage2, r.ribbon_bull) == (True, False)
    assert r.weight == pytest.approx(r.lrs_weight * 0.5)
    assert "partial confirmation" in r.notes


def test_no_confirmation_is_no_size(monkeypatch):
    up = rising()
    monkeypatch.setattr(R, "stage2", lambda _c: False)
    monkeypatch.setattr(R, "ribbon_bull", lambda _c: False)
    r = R.assess("SPY", up)
    assert r.weight == 0.0
    assert "no trend support" in r.notes


def test_the_weight_can_only_shrink_the_book():
    for seed in range(25):
        r = R.assess("SPY", rising(vol=0.01, seed=seed))
        assert 0.0 <= r.weight <= 1.0


# --- failure is degradation, never a guess ---------------------------------
@pytest.mark.parametrize("closes", [None, [], [100.0] * 10, [100.0] * 259])
def test_too_little_history_degrades_rather_than_inventing(closes):
    r = R.assess("SPY", closes)
    assert r.source == "degraded"
    assert r.weight == C.REGIME_DEGRADED_W


def test_a_degraded_regime_is_still_half_size_not_zero():
    """The two-tier policy: an ABSENT input degrades, it does not stand down.

    A regime that could not be measured is not evidence of a bad regime, and
    a cycle that refuses to trade because a price history was short would be
    a data outage masquerading as a risk decision.
    """
    assert R.degraded("SPY", "why").weight == C.REGIME_DEGRADED_W


# --- the pin: weeks expressed as trading days must mean the same thing -----
def test_the_daily_translation_agrees_with_a_true_weekly_resample():
    """`config` states 52/30/4 weeks are structural and were not retuned.

    The seam returns undated closes, so those windows are expressed as
    252/150/20 trading days. This asserts the translation is faithful: over
    many independent series, the daily form must reach the same Stage-2
    verdict as resampling to real weekly closes and applying 52/30/4 directly.
    """
    for seed in range(40):
        rnd = random.Random(seed)
        drift = rnd.uniform(-0.0010, 0.0010)
        px, day, dated = 100.0, date(2021, 1, 4), []
        while len(dated) < 700:                  # business days only
            if day.weekday() < 5:
                px *= 1.0 + drift + rnd.gauss(0, 0.008)
                dated.append((day, px))
            day += timedelta(days=1)

        daily = [p for _, p in dated]
        weekly_by_iso: dict[tuple, float] = {}
        for d, p in dated:                       # last close of each ISO week
            weekly_by_iso[d.isocalendar()[:2]] = p
        w = list(weekly_by_iso.values())

        sma30 = sum(w[-30:]) / 30
        sma30_prior = sum(w[-34:-4]) / 30
        weekly_verdict = w[-1] > sma30 and sma30 > sma30_prior

        assert R.stage2(daily) == weekly_verdict, (
            f"seed {seed}: daily translation disagreed with the weekly form")

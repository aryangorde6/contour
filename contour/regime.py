"""Position sizing from measured trend regime.

This module exists because of a measurement, not a hunch. Across sixteen
consecutive cycles on 2026-08-31 the LLM's regime call returned a size
multiplier of exactly 0.5 every time, with mutually contradictory prose
attached -- "implied is roughly double realized, which normally favors short
premium" in one call, "vol premium is NOT being paid" in another, same
market, same day. The model was anchoring on a number and narrating
afterwards. Half the book was being sized by an artifact.

So sizing moves here, to three published trend systems that were researched,
backtested and frozen before this hackathon existed:

  Stage-2      Weinstein Stage Analysis, mechanised: price above a RISING
               30-week SMA. Replicated in-house over 30.5 years and 397
               round-trips on 39 names -- pooled profit factor 3.53 (95% CI
               2.11-5.82), and 3.78 on a second out-of-sample universe.
  Ribbon       EMA ribbon stacked bullish above the 200 EMA. Validated long-
               only across 15 names in 10 sectors, 11 of 15 profitable.
  LRS-VT2      "Leverage for the Long Run" (Gayed & Bilello 2016, Dow Award)
               + conditional volatility scaling (Moreira & Muir 2017, Journal
               of Finance) + a two-speed trend ladder. 28-30% CAGR over 55
               years in backtest.

**How they combine, and why that split.** LRS-VT2 supplies the MAGNITUDE: it
is the only one of the three carrying an explicit position-sizing formula
(vol scaling `min(1, sigma_longrun/sigma_20d)`, a regime ladder, and an
overextension trim). Stage-2 and the ribbon supply CONFIRMATION: both are
binary states, not sizers, so they gate rather than scale. Both confirming
leaves the LRS weight alone; one confirming halves it; neither standing
means no trend support at all and the weight is zero.

**Two transfers, stated rather than hidden.** (1) Stage-2 and the ribbon were
validated on Indian equities and are used here on US ETFs. Stage-2 is
Weinstein -- US-origin literature returning home -- and the ribbon is generic
trend-following; neither was fitted to India. It is still a transfer.
(2) All three are LONG-EQUITY systems being used to size a SHORT-PREMIUM
book. The justification is that leveraged long equity and short option
premium are the same trade in disguise: both are short volatility and both
die in the same regime, which is exactly what the vol-scaling term measures.

**The windows are translated from weeks to trading days, not retuned.** The
source Pine runs Stage-2 on weekly bars; the DataSource seam returns undated
closes, so 52/30/4 weeks are expressed as 252/150/20 trading days. The source
header says the lengths are structural and must not be optimised, and they
have not been -- `tests/test_regime.py` pins the translation by asserting the
daily form agrees with a true weekly resampling on live data.

Pure functions, zero I/O, in the style of `gates.py`. Nothing here reaches an
order; it can only scale the NAV used for sizing, and it is bounded at 1.0.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Sequence

from . import config as C


@dataclass(frozen=True)
class Regime:
    """One underlying's trend state and the size weight it implies."""
    underlying: str
    stage2: bool
    ribbon_bull: bool
    lrs_weight: float
    weight: float
    source: str                       # "measured" | "degraded"
    notes: str

    def as_dict(self) -> dict:
        d = asdict(self)
        d["lrs_weight"] = round(self.lrs_weight, 3)
        d["weight"] = round(self.weight, 3)
        return d


def _sma(x: Sequence[float], n: int) -> float:
    return sum(x[-n:]) / n


def _ema(x: Sequence[float], n: int) -> float:
    k = 2.0 / (n + 1.0)
    e = x[0]
    for v in x[1:]:
        e = v * k + e * (1.0 - k)
    return e


def _stdev(x: Sequence[float]) -> float:
    m = sum(x) / len(x)
    return math.sqrt(sum((v - m) ** 2 for v in x) / (len(x) - 1))


def stage2(closes: Sequence[float]) -> bool:
    """Weinstein Stage 2: above a rising long-term SMA.

    The traded system also requires a break to a new 52-week closing high;
    that is its ENTRY. What is used here is `inStage2`, the persistent regime
    state the source script exposes separately -- a state variable, which is
    the only kind of signal that means anything over a four-day window.
    """
    s = _sma(closes, C.STAGE2_SMA_D)
    prior = _sma(closes[:-C.STAGE2_RISING_D], C.STAGE2_SMA_D)
    return closes[-1] > s and s > prior


def at_52w_high(closes: Sequence[float]) -> bool:
    """The Stage-2 breakout trigger. Reported, never sized on."""
    return closes[-1] > max(closes[-(C.STAGE2_ANCHOR_D + 1):-1])


def ribbon_bull(closes: Sequence[float]) -> bool:
    """Ribbon stacked bullish above the trend anchor ("light" mode, the default).

    Price is deliberately NOT required above the fast EMA -- in the source
    strategy that is where the pullback entry happens.
    """
    f, m, _, slow = C.RIBBON_EMAS
    return (_ema(closes, f) > _ema(closes, m)
            and _ema(closes, m) > _ema(closes, slow)
            and closes[-1] > _ema(closes, slow))


def lrs_weight(closes: Sequence[float]) -> float:
    """LRS-VT2 target weight: regime ladder x vol veto x overextension trim."""
    px = closes[-1]
    slow, fast = _sma(closes, C.LRS_SLOW_D), _sma(closes, C.LRS_FAST_D)

    rets = [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))]
    n = min(C.LRS_LONGRUN_D, len(rets))
    vol20 = _stdev(rets[-C.LRS_VOL_D:]) * math.sqrt(252)
    vol_lr = _stdev(rets[-n:]) * math.sqrt(252)

    # Two-speed ladder. The warning rung is above the slow SMA but below the
    # fast one -- the 1987/2020 breakdown zone, held at partial size.
    if px > slow:
        base = 1.0 if px > fast else C.LRS_WARN_W
    else:
        base = 1.0 if px > fast else 0.0

    # Conditional vol scaling: trim only when recent vol runs hot against its
    # own long-run level. An unconditional scaler de-levers in calm markets too.
    veto = min(1.0, vol_lr / vol20) if vol20 > C.LRS_VETO_K * vol_lr else 1.0

    # Overextension trim: cap unprotected crash room after a parabolic run.
    ext = px / slow - 1.0
    trim = C.LRS_EXT_CAP / ext if (C.LRS_EXT_CAP > 0 and ext > C.LRS_EXT_CAP) else 1.0

    return max(0.0, min(1.0, base * veto * trim))


def degraded(underlying: str, why: str) -> Regime:
    """Not enough history to measure. Half size, exactly as before this module.

    This mirrors the two-tier policy the brain already uses: an ABSENT input
    degrades and keeps trading, it does not fail the cycle. A regime that
    cannot be measured is not evidence of a bad regime.
    """
    return Regime(underlying, False, False, C.REGIME_DEGRADED_W,
                  C.REGIME_DEGRADED_W, "degraded", why)


def assess(underlying: str, closes: Sequence[float]) -> Regime:
    """The composite weight. Bounded at 1.0 -- this can only shrink the book."""
    if closes is None or len(closes) < C.REGIME_MIN_BARS:
        return degraded(underlying,
                        f"only {0 if not closes else len(closes)} daily closes, "
                        f"need {C.REGIME_MIN_BARS}")

    s2, rb = stage2(closes), ribbon_bull(closes)
    lrs = lrs_weight(closes)

    confirmations = int(s2) + int(rb)
    if confirmations == 0:
        w, note = 0.0, "no trend support: neither Stage-2 nor the ribbon stands"
    elif confirmations == 1:
        w = lrs * 0.5
        note = ("partial confirmation: "
                f"{'Stage-2' if s2 else 'ribbon'} only, LRS weight halved")
    else:
        w, note = lrs, "Stage-2 and ribbon both confirm; LRS weight taken whole"

    return Regime(underlying, s2, rb, lrs, max(0.0, min(1.0, w)),
                  "measured", note)

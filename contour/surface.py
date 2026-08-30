"""Three numbers per underlying, off one chain call. Pure functions only --
the I/O lives in data.py so every measurement is replayable from a fixture.
"""
from __future__ import annotations

import math
from typing import Sequence

from . import config as C
from .models import Measurement


def realized_vol(closes: Sequence[float]) -> float:
    """Annualized close-to-close stdev of log returns."""
    if len(closes) < 3:
        raise ValueError(f"need >=3 closes, got {len(closes)}")
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0 and b > 0]
    n = len(rets)
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / (n - 1)
    return math.sqrt(var) * math.sqrt(252) * 100.0   # vol POINTS, e.g. 7.6


def atm_iv(quotes: Sequence[tuple[float, float]], spot: float, k: int = 4) -> float:
    """Open-interest-weighted mean IV of the k strikes nearest spot.

    quotes: (strike, iv) pairs -- nulls must already be dropped by the caller,
    because G6 treats a missing Greek as a hard veto rather than a zero.
    """
    if not quotes:
        raise ValueError("no quotes with IV")
    near = sorted(quotes, key=lambda q: abs(q[0] - spot))[:k]
    return sum(iv for _, iv in near) / len(near) * 100.0


def skew_25(put_iv: float, call_iv: float) -> float:
    """IV(25d put) - IV(25d call), in vol points. Positive = puts richer."""
    return (put_iv - call_iv) * 100.0


def skew_z(underlying: str, skew: float) -> float:
    ref, sd = C.SKEW_PRIOR[underlying]
    return (skew - ref) / sd


def measure(underlying: str, spot: float, closes: Sequence[float],
            atm_quotes: Sequence[tuple[float, float]],
            put25_iv: float, call25_iv: float) -> Measurement:
    iv = atm_iv(atm_quotes, spot)
    rv = realized_vol(closes)
    sk = skew_25(put25_iv, call25_iv)
    return Measurement(
        underlying=underlying, spot=spot, atm_iv=iv, rv10=rv,
        # A RATIO, never an absolute vol-point difference: a difference across
        # names of different vol level is ~96% correlated with IV level, which
        # silently turns "sell the richest vol" into "sell the jumpiest name".
        vrp_ratio=iv / max(rv, C.RV_FLOOR),
        skew25=sk, skew_z=skew_z(underlying, sk),
    )

"""Turn a chosen structure into a concrete, submittable multi-leg order.

The single most dangerous line in this file is the limit price sign. Alpaca's
mleg convention is: NEGATIVE limit_price = net credit, POSITIVE = net debit.
Getting it backwards on a credit spread either rejects outright or fills
terribly. It has its own function and six tests.
"""
from __future__ import annotations

import math
from math import gcd
from typing import Literal, Sequence

from . import config as C
from .models import Candidate, Leg, Structure

Direction = Literal["credit", "debit"]


def net_limit_price(net_premium: float, direction: Direction) -> float:
    """Alpaca mleg sign convention, in one place.

    net_premium is always given as a POSITIVE magnitude in dollars per share.
    A credit we receive is submitted NEGATIVE; a debit we pay is POSITIVE.
    """
    if net_premium < 0:
        raise ValueError(
            f"pass a positive magnitude and state the direction; got {net_premium}"
        )
    if direction == "credit":
        return -round(net_premium, 2)
    if direction == "debit":
        return round(net_premium, 2)
    raise ValueError(f"direction must be 'credit' or 'debit', got {direction!r}")


def ladder_prices(net_credit_mid: float) -> list[float]:
    """Three rungs, each a signed limit price. Progressively worse for us."""
    return [net_limit_price(rung * net_credit_mid, "credit")
            for rung in C.LADDER_RUNGS]


def validate_legs(legs: Sequence[Leg]) -> tuple[bool, str]:
    """Alpaca mleg constraints: <=4 legs, no equity leg, GCD(ratio_qty) == 1."""
    if not legs:
        return False, "no legs"
    if len(legs) > 4:
        return False, f"{len(legs)} legs exceeds the 4-leg mleg maximum"
    ratios = [l.ratio_qty for l in legs]
    if any(r < 1 for r in ratios):
        return False, f"ratio_qty must be >= 1, got {ratios}"
    g = ratios[0]
    for r in ratios[1:]:
        g = gcd(g, r)
    if g != 1:
        return False, f"GCD(ratio_qty)={g} must be 1 -- use 1:2, never 2:4"
    if len({l.expiration_date for l in legs}) != 1:
        return False, "all legs must share one expiration"
    return True, f"{len(legs)} legs valid"


def net_credit_from_mids(legs: Sequence[Leg]) -> float:
    """Positive = we receive. Shorts add, longs subtract."""
    return sum((l.mid if l.is_short else -l.mid) for l in legs)


def contracts_for(nav: float, max_loss_per_contract: float) -> int:
    """floor(1.0% NAV / max loss), minimum 1. Returns 0 if even one contract
    would breach the per-position cap -- the caller skips the name."""
    if max_loss_per_contract <= 0:
        raise ValueError("max_loss_per_contract must be positive")
    cap = C.MAX_POSITION_RISK_PCT * nav
    if max_loss_per_contract > cap:
        return 0
    return max(1, math.floor(cap / max_loss_per_contract))


def pick_by_delta(candidates: Sequence[Leg], target: float,
                  band: tuple[float, float]) -> Leg | None:
    """Nearest |delta| to target, but only within the band. Never coerces a
    null delta -- those are filtered upstream and vetoed by G6."""
    lo, hi = band
    inside = [l for l in candidates
              if l.delta is not None and lo <= abs(l.delta) <= hi]
    if not inside:
        return None
    return min(inside, key=lambda l: abs(abs(l.delta) - target))


def build(underlying: str, structure: Structure, legs: Sequence[Leg],
          nav: float) -> Candidate | None:
    """Assemble a Candidate. Returns None if the structure cannot be built."""
    if structure == "NO_TRADE":
        return None
    ok, _ = validate_legs(legs)
    if not ok:
        return None
    wing = C.WING_WIDTH[underlying]
    credit = net_credit_from_mids(legs)
    if credit <= 0:
        return None
    # An iron condor can only lose on one side, so max loss is one wing less
    # the whole credit -- not two wings.
    max_loss = (wing - credit) * 100.0
    n = contracts_for(nav, max_loss)
    if n == 0:
        return None
    return Candidate(
        underlying=underlying, structure=structure, legs=tuple(legs),
        net_credit=credit, wing_width=wing, contracts=n,
        max_loss_per_contract=max_loss,
    )


def to_cli_legs(cand: Candidate, closing: bool = False) -> list[dict]:
    """The --legs payload. Closing reverses every intent; the CALLER is
    responsible for ordering shorts first on a legout (see manage.py)."""
    out = []
    for l in cand.legs:
        if closing:
            side = "buy" if l.is_short else "sell"
            intent = "buy_to_close" if l.is_short else "sell_to_close"
        else:
            side = l.side
            intent = "sell_to_open" if l.is_short else "buy_to_open"
        out.append({"symbol": l.symbol, "ratio_qty": str(l.ratio_qty),
                    "side": side, "position_intent": intent})
    return out

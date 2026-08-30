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


def _with_side(leg: Leg, side: Literal["buy", "sell"]) -> Leg:
    from dataclasses import replace
    return replace(leg, side=side)


def _wing(legs: Sequence[Leg], short: Leg, wing_width: float,
          above: bool) -> Leg | None:
    """The protective leg, wing_width away from the short strike."""
    target = short.strike + wing_width if above else short.strike - wing_width
    same_type = [l for l in legs if l.option_type == short.option_type
                 and ((l.strike > short.strike) if above else (l.strike < short.strike))]
    if not same_type:
        return None
    return min(same_type, key=lambda l: abs(l.strike - target))


def assemble(structure: Structure, legs: Sequence[Leg],
             underlying: str) -> list[Leg] | None:
    """Turn a chosen structure plus a chain into concrete, sided legs.

    Long wings are picked by STRIKE DISTANCE, not by delta: a fixed wing width
    is what makes max loss knowable in advance, which is what G3 sizes against.
    G7 still range-checks the resulting wing delta afterwards.
    """
    if structure == "NO_TRADE":
        return None
    wing_width = C.WING_WIDTH[underlying]
    puts = [l for l in legs if l.option_type == "put"]
    calls = [l for l in legs if l.option_type == "call"]
    out: list[Leg] = []

    if structure in ("PUT_CS", "CONDOR"):
        sp = pick_by_delta(puts, 0.13, C.SHORT_DELTA_BAND)
        if sp is None:
            return None
        lp = _wing(puts, sp, wing_width, above=False)
        if lp is None:
            return None
        out += [_with_side(sp, "sell"), _with_side(lp, "buy")]

    if structure in ("CALL_CS", "CONDOR"):
        sc = pick_by_delta(calls, 0.13, C.SHORT_DELTA_BAND)
        if sc is None:
            return None
        lc = _wing(calls, sc, wing_width, above=True)
        if lc is None:
            return None
        out += [_with_side(sc, "sell"), _with_side(lc, "buy")]

    return out or None

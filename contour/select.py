"""The structure map. This is the differentiator, and it is four lines.

Everyone sells iron condors. A condor sells BOTH wings unconditionally, which
means half the time you are selling the underpriced side. Contour measures
25-delta skew first and sells only the rich side:

    vrp_ratio < 1.30   -> NO_TRADE            implied is not rich enough
    skew_z >= +0.8     -> PUT_CREDIT_SPREAD   puts rich, sell puts only
    skew_z <= -0.8     -> CALL_CREDIT_SPREAD  calls rich, sell calls only
    otherwise          -> IRON_CONDOR         both sides fairly priced

The consequence is visible in the order history, not just the README: some
sessions the account shows put spreads, some call spreads, some condors.
"""
from __future__ import annotations

from . import config as C
from .models import Measurement, Structure


def choose_structure(m: Measurement) -> tuple[Structure, str]:
    """Returns (structure, reason). The reason is journaled either way, so a
    no-trade cycle is as auditable as a trade."""
    if m.vrp_ratio < C.VRP_RATIO_FLOOR:
        return "NO_TRADE", (
            f"VRP_TOO_LOW: {m.underlying} implied/realized {m.vrp_ratio:.2f} "
            f"< {C.VRP_RATIO_FLOOR:.2f} -- not paid enough to sell"
        )
    if m.skew_z >= C.SKEW_Z_TRIGGER:
        return "PUT_CS", (
            f"PUT_SKEW_RICH: {m.underlying} skew_z {m.skew_z:+.2f} "
            f">= {C.SKEW_Z_TRIGGER:+.2f} -- selling puts only, not the cheap calls"
        )
    if m.skew_z <= -C.SKEW_Z_TRIGGER:
        return "CALL_CS", (
            f"CALL_SKEW_RICH: {m.underlying} skew_z {m.skew_z:+.2f} "
            f"<= {-C.SKEW_Z_TRIGGER:+.2f} -- selling calls only, not the cheap puts"
        )
    return "CONDOR", (
        f"SKEW_NEUTRAL: {m.underlying} skew_z {m.skew_z:+.2f} inside "
        f"+/-{C.SKEW_Z_TRIGGER:.2f} -- both sides fairly priced, selling both"
    )

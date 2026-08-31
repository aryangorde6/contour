"""Session and phase resolution. Cron never trusts its own firing time."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time

from . import config as C


BELL = time(9, 30)          # the open. Nothing before it is a session.
PREOPEN_FROM = time(9, 0)


def is_preopen(now_et: datetime) -> bool:
    """A contest weekday, before the bell.

    `resolve` calls this window CLOSED, correctly -- nothing may trade at
    09:20 ET. But the 13:20 UTC cron exists to plan the day's event blackouts
    before the open, and the phase alone cannot tell that cycle apart from a
    Sunday. This can.
    """
    return (now_et.date() in C.BOOK_RISK_RAMP
            and PREOPEN_FROM <= now_et.time() < BELL)


@dataclass(frozen=True)
class Phase:
    now_et: datetime
    is_open: bool
    mode: str          # TRADE | MANAGE_ONLY | FLATTEN | VERIFY_ONLY | CLOSED
    reason: str


def resolve(now_et: datetime, market_open: bool) -> Phase:
    day, t = now_et.date(), now_et.time()

    if day == C.VERIFY_ONLY_DAY:
        return Phase(now_et, market_open, "VERIFY_ONLY",
                     "G11: Fri Sep 4 publishes a no-trade decision and verifies")
    if day > C.VERIFY_ONLY_DAY or day not in C.BOOK_RISK_RAMP:
        return Phase(now_et, market_open, "CLOSED", f"{day} is outside the contest")
    if not market_open:
        return Phase(now_et, False, "CLOSED", "market closed")

    if day == C.FLATTEN_DAY and t >= C.FLATTEN_AT:
        return Phase(now_et, True, "FLATTEN",
                     f"G11: hard flatten at {C.FLATTEN_AT:%H:%M} ET")
    if day == C.LAST_ENTRY_DAY and t > C.LAST_ENTRY_TIME_THU:
        return Phase(now_et, True, "MANAGE_ONLY",
                     f"G11: no new entries after Thu {C.LAST_ENTRY_TIME_THU:%H:%M} ET")
    if t < C.ENTRY_OPEN:
        return Phase(now_et, True, "MANAGE_ONLY",
                     f"G11: before entry open {C.ENTRY_OPEN:%H:%M} ET")
    if t > C.ENTRY_CLOSE:
        return Phase(now_et, True, "MANAGE_ONLY",
                     f"G11: after entry close {C.ENTRY_CLOSE:%H:%M} ET")
    return Phase(now_et, True, "TRADE", "entry window open")

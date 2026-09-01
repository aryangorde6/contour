"""G1-G12. Twelve pure boolean functions, evaluated in fixed order before
every order. Each returns (pass, reason) and the reason is journaled whether
it passes or fails.

Zero I/O by construction: everything comes in through Candidate/Book/Context.
This module is written and tested before any strategy code exists, which is
what makes the write-up's risk section true rather than aspirational.
"""
from __future__ import annotations

from datetime import datetime

from . import config as C
from .models import Book, Candidate, Context

Result = tuple[bool, str]


def g1_capital_floor(cand: Candidate, book: Book, ctx: Context) -> Result:
    if book.nav < C.NAV_HARD_HALT:
        return False, f"G1 HARD HALT: NAV ${book.nav:,.0f} < ${C.NAV_HARD_HALT:,.0f} (-4.0%)"
    if book.nav < C.NAV_NO_ENTRY:
        return False, f"G1 no new entries: NAV ${book.nav:,.0f} < ${C.NAV_NO_ENTRY:,.0f} (-3.0%)"
    return True, f"G1 ok: NAV ${book.nav:,.0f}"


def g2_daily_loss_halt(cand: Candidate, book: Book, ctx: Context) -> Result:
    limit = C.DAILY_LOSS_HALT_PCT * book.nav
    if book.session_pnl < limit:
        return False, f"G2 daily halt: session P&L ${book.session_pnl:,.0f} < ${limit:,.0f}"
    return True, f"G2 ok: session P&L ${book.session_pnl:,.0f}"


def g3_book_risk_ramp(cand: Candidate, book: Book, ctx: Context) -> Result:
    day = ctx.now_et.date()
    ramp = C.BOOK_RISK_RAMP.get(day, 0.0)
    cap = ramp * book.nav
    per_cap = C.MAX_POSITION_RISK_PCT * book.nav
    if cand.max_loss_per_contract > per_cap:
        return False, (f"G3 single contract ${cand.max_loss_per_contract:,.0f} "
                       f"exceeds per-position cap ${per_cap:,.0f}")
    if cand.total_max_loss > per_cap:
        return False, (f"G3 position risk ${cand.total_max_loss:,.0f} > "
                       f"{C.MAX_POSITION_RISK_PCT:.2%} NAV cap ${per_cap:,.0f}")
    projected = book.open_risk + cand.total_max_loss
    if projected > cap:
        return False, (f"G3 book risk ${projected:,.0f} would exceed "
                       f"{ramp:.0%} ramp cap ${cap:,.0f} for {day}")
    return True, f"G3 ok: book ${projected:,.0f} / ${cap:,.0f} ({ramp:.0%} ramp)"


def g4_concentration(cand: Candidate, book: Book, ctx: Context) -> Result:
    if len(book.positions) >= C.MAX_CONCURRENT_POSITIONS:
        return False, f"G4 at max {C.MAX_CONCURRENT_POSITIONS} concurrent positions"
    if book.count_for(cand.underlying) >= C.MAX_POSITIONS_PER_UNDERLYING:
        return False, f"G4 already {C.MAX_POSITIONS_PER_UNDERLYING} positions in {cand.underlying}"
    if ctx.opened_this_cycle.count(cand.underlying) >= C.MAX_NEW_PER_UNDERLYING_PER_CYCLE:
        return False, f"G4 {cand.underlying} already opened this cycle"
    return True, f"G4 ok: {len(book.positions)} open, {book.count_for(cand.underlying)} in {cand.underlying}"


def g5_liquidity(cand: Candidate, book: Book, ctx: Context) -> Result:
    for leg in cand.legs:
        if not leg.tradable:
            return False, f"G5 {leg.symbol} not tradable"
        if leg.open_interest < C.MIN_OPEN_INTEREST:
            return False, f"G5 {leg.symbol} OI {leg.open_interest} < {C.MIN_OPEN_INTEREST}"
        if leg.close_price is None:
            return False, f"G5 {leg.symbol} has no close_price"
        if leg.mid <= 0:
            return False, f"G5 {leg.symbol} non-positive mid"
        if leg.quote_age_s is not None and leg.quote_age_s > C.MAX_QUOTE_AGE_S:
            return False, (f"G5 {leg.symbol} quote is {leg.quote_age_s / 60:.1f} min "
                           f"stale (max {C.MAX_QUOTE_AGE_S / 60:.0f} min)")
        pct_ok = leg.spread <= C.MAX_SPREAD_PCT_OF_MID * leg.mid
        abs_ok = leg.spread <= C.MAX_SPREAD_ABS
        if not (pct_ok or abs_ok):
            return False, (f"G5 {leg.symbol} spread ${leg.spread:.2f} "
                           f"({leg.spread / leg.mid:.0%} of mid) exceeds both "
                           f"{C.MAX_SPREAD_PCT_OF_MID:.0%} and ${C.MAX_SPREAD_ABS:.2f}")

    # Package friction: one round trip = crossing every leg's spread twice.
    friction = sum(leg.spread for leg in cand.legs) * 2.0
    budget = C.MAX_ROUND_TRIP_FRICTION_PCT_OF_CREDIT * cand.net_credit
    if friction > budget:
        return False, (f"G5 round-trip friction ${friction:.2f} > "
                       f"{C.MAX_ROUND_TRIP_FRICTION_PCT_OF_CREDIT:.0%} of "
                       f"${cand.net_credit:.2f} credit (${budget:.2f})")
    return True, (f"G5 ok: {len(cand.legs)} legs liquid, "
                  f"round-trip friction ${friction:.2f} / ${budget:.2f}")


def g6_greeks_validity(cand: Candidate, book: Book, ctx: Context) -> Result:
    """A missing Greek is a hard veto, never coerced to zero. Also structurally
    excludes 0DTE, which never returns Greeks (Black-Scholes divides by
    time-to-expiry)."""
    for leg in cand.legs:
        if leg.delta is None:
            return False, f"G6 {leg.symbol} delta is null -- hard veto"
        if leg.implied_volatility is None:
            return False, f"G6 {leg.symbol} IV is null -- hard veto"
    return True, f"G6 ok: Greeks present on all {len(cand.legs)} legs"


def g7_delta_band(cand: Candidate, book: Book, ctx: Context) -> Result:
    lo_s, hi_s = C.SHORT_DELTA_BAND
    lo_l, hi_l = C.LONG_DELTA_BAND
    for leg in cand.legs:
        if leg.delta is None:
            return False, f"G7 {leg.symbol} null delta (G6 should have caught this)"
        d = abs(leg.delta)
        if leg.is_short and not (lo_s <= d <= hi_s):
            return False, f"G7 short {leg.symbol} |delta| {d:.3f} outside [{lo_s}, {hi_s}]"
        if not leg.is_short and not (lo_l <= d <= hi_l):
            return False, f"G7 wing {leg.symbol} |delta| {d:.3f} outside [{lo_l}, {hi_l}]"
    if cand.structure == "CONDOR":
        nd = cand.net_delta
        if nd is None or abs(nd) > C.MAX_NET_DELTA_CONDOR:
            return False, f"G7 condor net delta {nd} exceeds +/-{C.MAX_NET_DELTA_CONDOR}"
    return True, "G7 ok: all legs inside delta bands"


def g8_expiry_lock(cand: Candidate, book: Book, ctx: Context) -> Result:
    for leg in cand.legs:
        if leg.expiration_date != C.EXPIRY:
            return False, (f"G8 {leg.symbol} expires {leg.expiration_date}, "
                           f"locked to {C.EXPIRY}")
    return True, f"G8 ok: all legs expire {C.EXPIRY}"


def g9_credit_floor(cand: Candidate, book: Book, ctx: Context) -> Result:
    """Structure-aware: a one-sided vertical at 13-delta pays roughly half what
    a condor does, so a single flat floor cannot serve both. See config.py."""
    pct = C.MIN_CREDIT_PCT_OF_WING.get(cand.structure)
    if pct is None:
        return False, f"G9 no credit floor defined for structure {cand.structure}"
    floor = pct * cand.wing_width
    worst_rung = min(C.LADDER_RUNGS) * cand.net_credit
    if worst_rung < floor:
        return False, (f"G9 rung-3 credit ${worst_rung:.2f} < floor ${floor:.2f} "
                       f"({pct:.0%} of ${cand.wing_width:.2f} wing, {cand.structure})")
    return True, (f"G9 ok: credit ${cand.net_credit:.2f}, rung-3 ${worst_rung:.2f} "
                  f"vs floor ${floor:.2f}")


def g10_event_blackout(cand: Candidate, book: Book, ctx: Context) -> Result:
    for b in ctx.blackouts:
        if b.start <= ctx.now_et <= b.end:
            return False, f"G10 blackout: {b.reason} ({b.start:%H:%M}-{b.end:%H:%M} ET)"
    for day, start, end, reason in C.FALLBACK_BLACKOUTS:
        if ctx.now_et.date() == day and start <= ctx.now_et.time() <= end:
            return False, f"G10 fallback blackout: {reason}"
    return True, "G10 ok: outside all blackout windows"


def g11_schedule(cand: Candidate, book: Book, ctx: Context) -> Result:
    now = ctx.now_et
    day, clock = now.date(), now.time()
    if day == C.VERIFY_ONLY_DAY:
        return False, "G11 VERIFY_ONLY: Fri Sep 4 is publish-and-verify, no entries"
    if day not in C.BOOK_RISK_RAMP:
        return False, f"G11 {day} is outside the contest window"
    if clock < C.ENTRY_OPEN:
        return False, f"G11 {clock:%H:%M} ET before entry open {C.ENTRY_OPEN:%H:%M}"
    if clock > C.ENTRY_CLOSE:
        return False, f"G11 {clock:%H:%M} ET after entry close {C.ENTRY_CLOSE:%H:%M}"
    if day == C.LAST_ENTRY_DAY and clock > C.LAST_ENTRY_TIME_THU:
        return False, f"G11 no new entries after Thu {C.LAST_ENTRY_TIME_THU:%H:%M} ET"
    if ctx.llm_no_new_entries_after and now > ctx.llm_no_new_entries_after:
        return False, f"G11 past LLM cutoff {ctx.llm_no_new_entries_after:%H:%M} ET"
    return True, f"G11 ok: {clock:%H:%M} ET on {day}"


def g12_kill_switch(cand: Candidate, book: Book, ctx: Context) -> Result:
    if ctx.halt_file_present:
        return False, "G12 HALT file present -- all trading stopped"
    if not ctx.client_order_id:
        return False, "G12 no client_order_id -- refusing unidentifiable order"
    if ctx.client_order_id in ctx.seen_client_order_ids:
        return False, f"G12 client_order_id {ctx.client_order_id} already used"
    return True, f"G12 ok: {ctx.client_order_id}"


GATES = (
    g1_capital_floor, g2_daily_loss_halt, g3_book_risk_ramp, g4_concentration,
    g5_liquidity, g6_greeks_validity, g7_delta_band, g8_expiry_lock,
    g9_credit_floor, g10_event_blackout, g11_schedule, g12_kill_switch,
)


def evaluate(cand: Candidate, book: Book, ctx: Context) -> tuple[bool, list[str]]:
    """Run every gate in fixed order. Returns (allowed, all_reasons).

    Every reason is returned, pass or fail, because the journal records the
    full evaluation -- that is what makes a no-trade cycle auditable.
    """
    reasons: list[str] = []
    allowed = True
    for gate in GATES:
        ok, reason = gate(cand, book, ctx)
        reasons.append(reason)
        if not ok:
            allowed = False
            break
    return allowed, reasons

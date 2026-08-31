"""One idempotent cycle. Cron fires this; it decides what, if anything, to do.

Every cycle journals a record even when it does nothing. A no-trade cycle with
its reason is as auditable as a fill, and that is what lets a judge reconcile
our claims against the order history they pull from Alpaca independently.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Sequence

from . import config as C
from . import gates, select, state, structures as S, surface
from .clock import Phase, resolve
from .data import DataSource
from .execute import CLIBroker, submit_with_ladder
from .journal import Journal
from .manage import ManagedPosition, close_position, should_exit
from .mind import Mind
from .models import Blackout, Book, Context, Leg, Measurement, OpenPosition

HALT_FILE = Path("HALT")


def measure_underlying(ds: DataSource, underlying: str,
                       expiry: date) -> tuple[Measurement, list[Leg]] | None:
    """Three numbers off one chain call. Returns None if the chain cannot be
    measured -- which is a journaled no-trade, not an exception."""
    spot = ds.spot(underlying)
    legs = ds.legs(underlying, expiry, spot)
    if not legs:
        return None
    closes = ds.closes(underlying, 11)

    with_iv = [l for l in legs if l.implied_volatility is not None]
    if not with_iv:
        return None
    atm_quotes = [(l.strike, l.implied_volatility) for l in with_iv]

    puts = [l for l in with_iv if l.option_type == "put"]
    calls = [l for l in with_iv if l.option_type == "call"]
    p25 = S.pick_by_delta(puts, 0.25, (0.18, 0.32))
    c25 = S.pick_by_delta(calls, 0.25, (0.18, 0.32))
    if p25 is None or c25 is None:
        return None

    m = surface.measure(underlying, spot, closes, atm_quotes,
                        p25.implied_volatility, c25.implied_volatility)
    return m, legs


def order_base_id(underlying: str, structure: str, now: datetime) -> str:
    """Deterministic per (underlying, structure, 15-min bucket), so a retried
    or double-fired cron cannot open the same position twice."""
    bucket = now.strftime("%Y%m%dT%H") + f"{now.minute // 15:02d}"
    h = hashlib.sha256(f"{underlying}{structure}{bucket}".encode()).hexdigest()[:8]
    return f"contour-{underlying.lower()}-{h}"


@dataclass
class CycleResult:
    mode: str
    reason: str
    measurements: list[dict]
    decisions: list[dict]
    exits: list[dict]


def run_cycle(
    ds: DataSource,
    broker: CLIBroker,
    now_et: datetime,
    market_open: bool,
    journal: Journal,
    open_positions: Sequence[ManagedPosition] = (),
    blackouts: Sequence[Blackout] = (),
    llm_cutoff: datetime | None = None,
    cycle: int = 0,
    dry: bool = False,
    mind: Mind | None = None,
) -> CycleResult:
    phase: Phase = resolve(now_et, market_open)
    halted = HALT_FILE.exists()
    if halted:
        phase = Phase(now_et, market_open, "HALTED",
                      "G12: committed HALT file present")

    journal.append({"event": "cycle_start", "cycle": cycle,
                    "now_et": now_et.isoformat(), "mode": phase.mode,
                    "reason": phase.reason, "dry": dry})

    # --- exits first, always. A position must be manageable even when the
    # --- entry window is shut.
    exits: list[dict] = []
    for pos in open_positions:
        try:
            m = measure_underlying(ds, pos.candidate.underlying, C.EXPIRY)
            spot = m[0].spot if m else 0.0
            mark = abs(S.net_credit_from_mids(pos.candidate.legs))
            do_exit, why = should_exit(pos, mark, spot, now_et)
        except Exception as exc:                             # noqa: BLE001
            do_exit, why, mark = False, f"EXIT_CHECK_FAILED: {exc}", 0.0
        rec = {"underlying": pos.candidate.underlying, "order_id": pos.order_id,
               "mark": round(mark, 3), "exit": do_exit, "reason": why}
        exits.append(rec)
        journal.append({"event": "exit_check", **rec})
        if do_exit and not dry:
            escalate = (now_et.date() == C.FLATTEN_DAY
                        and now_et.time() >= C.MARKET_ESCALATION_AT)
            out = close_position(broker, pos, mark,
                                 f"{pos.order_id}-x", journal.append,
                                 escalate=escalate)
            journal.append({"event": "exit_done", **out})

    if phase.mode != "TRADE":
        journal.append({"event": "cycle_end", "cycle": cycle,
                        "mode": phase.mode, "entries": 0})
        state.heartbeat(cycle, phase.mode, phase.reason)
        return CycleResult(phase.mode, phase.reason, [], [], exits)

    # --- the advisory layer. It can only shrink what follows: blackouts add
    # --- veto windows, the multiplier scales sizing DOWN, the cutoff moves
    # --- the entry deadline EARLIER. Nothing here can widen a limit.
    multiplier = 1.0
    if mind is not None:
        adv_b = mind.blackouts(now_et.date())
        adv_r = mind.regime(now_et.date(), {})
        blackouts = tuple(blackouts) + adv_b.blackouts
        multiplier = min(adv_b.multiplier, adv_r.multiplier, 1.0)
        if adv_r.no_new_entries_after is not None:
            llm_cutoff = (min(llm_cutoff, adv_r.no_new_entries_after)
                          if llm_cutoff else adv_r.no_new_entries_after)
        journal.append({"event": "mind", "brain": mind.brain,
                        "blackouts": adv_b.source,
                        "regime": adv_r.source, "multiplier": multiplier,
                        "cutoff": llm_cutoff.isoformat() if llm_cutoff else None,
                        "notes": f"{adv_b.notes} | {adv_r.notes}"})

    # --- entries
    acct = broker.account()
    nav = float(acct.get("equity", 0))
    book = Book(nav=nav, session_pnl=0.0, positions=tuple(
        OpenPosition(p.candidate.underlying, p.candidate.structure,
                     p.candidate.contracts, p.candidate.max_loss_per_contract,
                     p.credit_received) for p in open_positions))

    measurements, decisions, opened = [], [], []
    for und in C.UNIVERSE:
        got = measure_underlying(ds, und, C.EXPIRY)
        if got is None:
            decisions.append({"underlying": und, "decision": "NO_TRADE",
                              "reason": "chain not measurable"})
            journal.append({"event": "decision", **decisions[-1]})
            continue
        m, legs = got
        measurements.append(m.as_dict())

        structure, why = select.choose_structure(m)
        if structure == "NO_TRADE":
            decisions.append({"underlying": und, "decision": "NO_TRADE",
                              "reason": why, **m.as_dict()})
            journal.append({"event": "decision", **decisions[-1]})
            continue

        sided = S.assemble(structure, legs, und)
        # The multiplier is applied to the NAV used for SIZING only, never to
        # a risk threshold. multiplier 0.0 yields zero contracts and the name
        # is skipped, which is the intended "stand down" behaviour.
        cand = S.build(und, structure, sided, nav * multiplier) if sided else None
        if cand is None:
            decisions.append({"underlying": und, "decision": "NO_TRADE",
                              "reason": f"{structure}: could not assemble a "
                                        f"valid structure from the chain"})
            journal.append({"event": "decision", **decisions[-1]})
            continue

        base = order_base_id(und, structure, now_et)
        ctx = Context(now_et=now_et, halt_file_present=halted,
                      blackouts=tuple(blackouts), opened_this_cycle=tuple(opened),
                      llm_no_new_entries_after=llm_cutoff,
                      client_order_id=f"{base}-r1")
        allowed, reasons = gates.evaluate(cand, book, ctx)
        decisions.append({
            "underlying": und, "decision": structure if allowed else "VETOED",
            "reason": why, "gates": reasons, **m.as_dict(),
            "credit": round(cand.net_credit, 3), "contracts": cand.contracts,
            "max_loss_per_contract": round(cand.max_loss_per_contract, 2),
            "legs": [l.symbol for l in cand.legs],
        })
        journal.append({"event": "decision", **decisions[-1]})
        if not allowed or dry:
            continue

        if mind is not None:
            v = mind.confirm(und, structure, m.vrp_ratio, m.skew_z)
            journal.append({"event": "mind_confirm", "underlying": und,
                            "veto": v.veto, "reason": v.reason})
            if v.veto:
                continue

        rec = submit_with_ladder(broker, cand, base, journal.append)
        if rec["filled_qty"] > 0:
            opened.append(und)

    journal.append({"event": "cycle_end", "cycle": cycle, "mode": phase.mode,
                    "entries": len(opened)})
    state.heartbeat(cycle, phase.mode, phase.reason,
                    {"nav": nav, "entries": len(opened)})
    state.write("surface", measurements)
    state.write("decisions", decisions)
    return CycleResult(phase.mode, phase.reason, measurements, decisions, exits)

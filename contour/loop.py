"""One idempotent cycle. Cron fires this; it decides what, if anything, to do.

Every cycle journals a record even when it does nothing. A no-trade cycle with
its reason is as auditable as a fill, and that is what lets a judge reconcile
our claims against the order history they pull from Alpaca independently.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Sequence

from . import config as C
from . import gates, positions as P, select, state, structures as S, surface
from .clock import Phase, is_preopen, resolve
from .data import DataSource
from .execute import CLIBroker, submit_with_ladder
from .journal import Journal
from .manage import (ManagedPosition, close_position, flatten_due,
                     should_exit)
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


def cycle_bucket(now: datetime) -> str:
    """The 15-minute window a cycle belongs to. Shared by entry and exit ids so
    both are idempotent within a cycle and distinct across cycles."""
    return now.strftime("%Y%m%dT%H") + f"{now.minute // 15:02d}"


def order_base_id(underlying: str, structure: str, now: datetime) -> str:
    """Deterministic per (underlying, structure, 15-min bucket), so a retried
    or double-fired cron cannot open the same position twice."""
    h = hashlib.sha256(
        f"{underlying}{structure}{cycle_bucket(now)}".encode()).hexdigest()[:8]
    return f"contour-{underlying.lower()}-{h}"


def close_base_id(pos: ManagedPosition, now: datetime) -> str:
    """Exit ids MUST vary per cycle. Alpaca 422s on a reused client_order_id,
    and close_position only special-cases the uncovered-leg rejection -- so a
    constant base id means the first failed close attempt poisons every later
    one, including Thursday's flatten. The 15-minute bucket keeps a retried
    cron idempotent while letting the next cycle try again."""
    return f"{pos.order_id}-x{cycle_bucket(now)}"


def _reprice(pos: ManagedPosition, chain: Sequence[Leg]) -> tuple[Leg, ...] | None:
    """The position's legs at today's quotes, or None if the chain is missing
    any of them. Partial re-pricing would understate the cost to close."""
    quotes = {l.symbol: l for l in chain}
    out = []
    for l in pos.candidate.legs:
        q = quotes.get(l.symbol)
        if q is None or not (q.bid or q.ask):
            return None
        out.append(replace(l, bid=q.bid, ask=q.ask))
    return tuple(out)


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
    # One chain read per underlying per cycle. The exit check, the regime call
    # and the entry loop all ask the same question; asking it three times costs
    # three round trips and can return three different answers inside one
    # cycle, which is how a book gets sized against numbers it never showed.
    chains: dict[str, tuple[Measurement, list[Leg]] | None] = {}

    def measure(und: str) -> tuple[Measurement, list[Leg]] | None:
        if und not in chains:
            chains[und] = measure_underlying(ds, und, C.EXPIRY)
        return chains[und]

    exits: list[dict] = []
    live = list(open_positions)
    for pos in open_positions:
        try:
            m = measure(pos.candidate.underlying)
            # The stored legs carry their ENTRY quotes and never change, so
            # pricing off them freezes mark at the entry credit and the profit
            # target and stop can never fire. Re-price from today's chain.
            fresh = _reprice(pos, m[1]) if m else None
            if m is None or fresh is None:
                # No usable quotes. Do NOT fall back to spot 0.0: that reads as
                # far below every short put and fires a phantom BREACH exit.
                # Only the clock rule is safe without market data.
                due = flatten_due(now_et)
                do_exit, mark = due is not None, 0.0
                why = due or ("HOLD_UNPRICED: no live quotes for "
                              f"{pos.candidate.underlying}; clock rule only")
            else:
                mark = abs(S.net_credit_from_mids(fresh))
                do_exit, why = should_exit(pos, mark, m[0].spot, now_et)
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
                                 close_base_id(pos, now_et), journal.append,
                                 escalate=escalate)
            journal.append({"event": "exit_done", **out})
            if out.get("closed"):
                live = [p for p in live if p.order_id != pos.order_id]
                P.save(live)

    if phase.mode != "TRADE":
        # The 13:20 UTC cron fires at 09:20 ET to plan the day's event
        # blackouts before the open. It resolves to CLOSED -- correctly, the
        # market is shut and nothing may trade -- and CLOSED returned here
        # having written a heartbeat and nothing else, so the pre-open cycle
        # never reached the advisory layer the docs credited it with.
        #
        # What it publishes is a PLAN, not a commitment: every TRADE cycle
        # still asks again, because a window computed three hours before the
        # open is not evidence about now. The value is that the day's schedule
        # is on the dashboard before the first trade, and the brain is proven
        # answering every morning rather than only when it matters.
        if mind is not None and is_preopen(now_et):
            adv = mind.blackouts(now_et.date())
            plan = {"day": now_et.date().isoformat(), "brain": mind.brain,
                    "source": adv.source, "notes": adv.notes,
                    "windows": [{"start_et": b.start.isoformat(),
                                 "end_et": b.end.isoformat(),
                                 "reason": b.reason} for b in adv.blackouts]}
            journal.append({"event": "plan", **plan})
            state.write("plan", plan)
        journal.append({"event": "cycle_end", "cycle": cycle,
                        "mode": phase.mode, "entries": 0})
        state.heartbeat(cycle, phase.mode, phase.reason,
                        {"brain": mind.brain if mind else "none"})
        return CycleResult(phase.mode, phase.reason, [], [], exits)

    # --- the advisory layer. It can only shrink what follows: blackouts add
    # --- veto windows, the multiplier scales sizing DOWN, the cutoff moves
    # --- the entry deadline EARLIER. Nothing here can widen a limit.
    multiplier = 1.0
    if mind is not None:
        adv_b = mind.blackouts(now_et.date())
        # The regime call sizes the entire book, so it has to see the surface
        # it is sizing against; it used to be handed an empty dict and asked
        # to judge the vol premium without being shown any. Measuring here is
        # free -- the entry loop reads the same cache a few lines down.
        vrp = {}
        for und in C.UNIVERSE:
            got = measure(und)
            if got is not None:
                vrp[und] = round(got[0].vrp_ratio, 3)
        adv_r = mind.regime(now_et.date(), vrp)
        blackouts = tuple(blackouts) + adv_b.blackouts
        multiplier = min(adv_b.multiplier, adv_r.multiplier, 1.0)
        if adv_r.no_new_entries_after is not None:
            llm_cutoff = (min(llm_cutoff, adv_r.no_new_entries_after)
                          if llm_cutoff else adv_r.no_new_entries_after)
        journal.append({"event": "mind", "brain": mind.brain,
                        "blackouts": adv_b.source,
                        "regime": adv_r.source, "multiplier": multiplier,
                        "vrp": vrp,
                        "cutoff": llm_cutoff.isoformat() if llm_cutoff else None,
                        "notes": f"{adv_b.notes} | {adv_r.notes}"})

    # --- entries
    acct = broker.account()
    nav = float(acct.get("equity", 0))
    state.point("equity", {"nav": round(nav, 2), "mode": phase.mode,
                           "cycle": cycle, "open": len(live)})
    book = Book(nav=nav, session_pnl=0.0, positions=tuple(
        OpenPosition(p.candidate.underlying, p.candidate.structure,
                     p.candidate.contracts, p.candidate.max_loss_per_contract,
                     p.credit_received) for p in live))

    measurements, decisions, opened = [], [], []

    # A zero multiplier is the advisory layer standing the agent down, not the
    # chain being unreadable. Without this the fail-closed path reports
    # "could not assemble a valid structure from the chain" for every name --
    # a brain outage reads in the journal as a market-data problem.
    if multiplier == 0.0:
        reason = ("STAND_DOWN: advisory layer returned multiplier 0 "
                  "(fail-closed brain or a hard event blackout); no entries "
                  "this cycle. Exits above still ran.")
        for und in C.UNIVERSE:
            decisions.append({"underlying": und, "decision": "NO_TRADE",
                              "reason": reason})
            journal.append({"event": "decision", **decisions[-1]})
        journal.append({"event": "cycle_end", "cycle": cycle,
                        "mode": phase.mode, "entries": 0,
                        "stand_down": True})
        state.heartbeat(cycle, phase.mode, phase.reason,
                        {"nav": nav, "entries": 0, "stand_down": True,
                         "brain": mind.brain if mind else "none"})
        state.write("decisions", decisions)
        return CycleResult(phase.mode, phase.reason, measurements, decisions,
                           exits)

    for und in C.UNIVERSE:
        got = measure(und)
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
            # Record what actually filled, not what was requested: a partial
            # fill leaves fewer contracts than the candidate describes, and an
            # exit sized off the request would try to close what we never had.
            held = replace(cand, contracts=int(rec["filled_qty"]))
            pos = ManagedPosition(
                candidate=held,
                credit_received=P.credit_from_fill(rec, cand.net_credit),
                opened_at=now_et, order_id=str(rec["order_id"]))
            live.append(pos)
            P.save(live)
            balanced = rec.get("legs_balanced", True)
            journal.append({"event": "position_opened", "underlying": und,
                            "order_id": pos.order_id,
                            "contracts": held.contracts,
                            "credit_received": round(pos.credit_received, 4),
                            "legs_balanced": balanced,
                            "open_book": len(live)})
            if not balanced:
                # execute.py has already raised the alarm. The consequence
                # belongs here: what is at the broker is not the defined-risk
                # structure G3 sized, so the book's risk is no longer a number
                # we can compute -- and opening more against an unknown is the
                # one thing that makes it worse. Stop. Exits above still ran.
                journal.append({"event": "entries_halted", "underlying": und,
                                "order_id": pos.order_id,
                                "reason": "unbalanced fill: book risk is no "
                                          "longer computable; no further "
                                          "entries this cycle, repair with "
                                          "ops/repair_book.py"})
                break

    journal.append({"event": "cycle_end", "cycle": cycle, "mode": phase.mode,
                    "entries": len(opened)})
    state.heartbeat(cycle, phase.mode, phase.reason,
                    {"nav": nav, "entries": len(opened),
                     "brain": mind.brain if mind else "none"})
    state.write("surface", measurements)
    state.write("decisions", decisions)
    return CycleResult(phase.mode, phase.reason, measurements, decisions, exits)

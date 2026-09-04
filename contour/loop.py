"""One idempotent cycle. Cron fires this; it decides what, if anything, to do.

Every cycle journals a record even when it does nothing. A no-trade cycle with
its reason is as auditable as a fill, and that is what lets a judge reconcile
our claims against the order history they pull from Alpaca independently.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Sequence

from . import config as C
from . import (gates, positions as P, profile as VP, regime, select,
               sleeve as SL, state, structures as S, surface)
from .clock import Phase, is_preopen, resolve
from .data import DataSource
from .execute import (CLIBroker, close_sleeve, place_sleeve_stop,
                      submit_sleeve_entry, submit_with_ladder)
from .journal import Journal
from .manage import (ManagedPosition, close_position, flatten_due,
                     should_exit)
from .mind import Mind
from .models import Blackout, Book, Context, Leg, Measurement, OpenPosition
from .regime import Regime

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


def sleeve_base_id(now: datetime) -> str:
    """One id per 15-minute bucket for the whole sleeve, entry and exit alike.

    Suffixed `-e` (entry), `-s` (protective stop) and `-x` (exit) at the point
    of use, so a double-fired cron inside one bucket cannot buy the sleeve
    twice -- Alpaca 422s on a reused client_order_id, which here is the
    desired outcome rather than an error to work around.
    """
    h = hashlib.sha256(
        f"{C.SLEEVE_UNDERLYING}SLEEVE{cycle_bucket(now)}".encode()).hexdigest()[:8]
    return f"contour-sleeve-{h}"


def close_base_id(pos: ManagedPosition, now: datetime) -> str:
    """Exit ids MUST vary per cycle. Alpaca 422s on a reused client_order_id,
    and close_position only special-cases the uncovered-leg rejection -- so a
    constant base id means the first failed close attempt poisons every later
    one, including Thursday's flatten. The 15-minute bucket keeps a retried
    cron idempotent while letting the next cycle try again.

    The `contour-` prefix is not decoration: attribution partitions the account
    on it. pos.order_id is the BROKER's order id, not the client id we chose,
    so without the prefix an exit the agent placed is indistinguishable from a
    human one -- and the position it closes gets charged to the operator."""
    return f"contour-x{cycle_bucket(now)}-{pos.order_id}"


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
    sleeve: dict = field(default_factory=dict)


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
    sleeve_position: SL.SleevePosition | None = None,
    sleeve_retired: bool = False,
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

    # Trend regime, cached the same way and for the same reason. A fixture
    # recorded before this module existed has no closes at this lookback, so
    # the seam raises and the name degrades to half size rather than failing
    # the cycle -- an unmeasurable regime is not evidence of a bad one.
    regimes: dict[str, Regime] = {}

    def trend(und: str) -> Regime:
        if und not in regimes:
            try:
                closes = ds.closes(und, C.REGIME_LOOKBACK)
            except Exception as exc:                             # noqa: BLE001
                regimes[und] = regime.degraded(und, f"closes unavailable: {exc}")
            else:
                regimes[und] = regime.assess(und, closes)
        return regimes[und]

    # Volume profile, cached like the regime and degraded the same way. The
    # `bars` seam is OPTIONAL: every fixture recorded before this module
    # existed lacks it, and those replays must still reproduce the decisions
    # they recorded. A missing method, an empty window or a failed read all
    # land on the same degraded profile, which vetoes nothing.
    profiles: dict[str, VP.Profile] = {}

    def traded(und: str) -> VP.Profile:
        if und not in profiles:
            fn = getattr(ds, "bars", None)
            if fn is None:
                profiles[und] = VP.degraded(und, "data source serves no bars")
            else:
                try:
                    bars = fn(und, C.PROFILE_LOOKBACK_D)
                except Exception as exc:                         # noqa: BLE001
                    profiles[und] = VP.degraded(und, f"bars unavailable: {exc}")
                else:
                    profiles[und] = VP.value_area(und, bars)
        return profiles[und]

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

    # --- the sleeve, exits first for the same reason the options book does:
    # --- a position must be manageable when the entry window is shut.
    sleeve_live = sleeve_position
    # The sleeve gets ONE entry. Set the moment it closes, for any reason, so
    # a stop that fires at 11:00 cannot be re-bought at 11:00 -- which would
    # spend a carve-out that funds exactly one stop loss twice over.
    retired = sleeve_retired
    sleeve_panel: dict[str, Any] = {
        "underlying": C.SLEEVE_UNDERLYING,
        "notional_ceiling": C.SLEEVE_NOTIONAL,
        "stop_pct": C.SLEEVE_STOP_PCT,
        "risk_budget_pct": C.SLEEVE_RISK_BUDGET_PCT,
    }

    def sleeve_spot() -> float | None:
        """The cached chain measurement first, a bare quote second.

        Cache first so the sleeve cannot pull a SECOND spot inside one cycle
        and price its stop off a number the dashboard never published. Bare
        quote second because the sleeve holds shares: an option chain that
        cannot be measured is not a reason to stop managing an equity
        position, and on a MANAGE_ONLY cycle nothing has read the chain at
        all.
        """
        got = chains.get(C.SLEEVE_UNDERLYING)
        if got is not None:
            return got[0].spot
        try:
            return float(ds.spot(C.SLEEVE_UNDERLYING))
        except Exception:                                    # noqa: BLE001
            return None

    if sleeve_live is not None:
        spx = sleeve_spot()
        reg_s = trend(C.SLEEVE_UNDERLYING)
        if spx is None:
            # Same rule as the options book, and the same reason: acting on a
            # price we do not have is worse than waiting. Unlike the options
            # book, the resting stop at the broker is still on duty meanwhile.
            due = SL.flatten_due(now_et)
            do_exit = due is not None
            why = due or (f"HOLD_UNPRICED: no quote for {sleeve_live.underlying}; "
                          f"clock rule only. The resting stop at "
                          f"${sleeve_live.stop_price:.2f} still applies.")
        else:
            do_exit, why = SL.should_exit(sleeve_live, spx, reg_s, now_et)
        chk = {"underlying": sleeve_live.underlying,
               "shares": sleeve_live.shares,
               "entry_price": round(sleeve_live.entry_price, 2),
               "stop_price": round(sleeve_live.stop_price, 2),
               "stop_resting": sleeve_live.stop_order_id is not None,
               "spot": round(spx, 2) if spx is not None else None,
               "unrealized": (round(sleeve_live.unrealized(spx), 2)
                              if spx is not None else None),
               "exit": do_exit, "reason": why}
        sleeve_panel["exit_check"] = chk
        journal.append({"event": "sleeve_exit_check", **chk})
        if do_exit and not dry:
            escalate = (now_et.date() == C.FLATTEN_DAY
                        and now_et.time() >= C.MARKET_ESCALATION_AT)
            try:
                out = close_sleeve(
                    broker, sleeve_live,
                    spx if spx is not None else sleeve_live.entry_price,
                    sleeve_base_id(now_et), journal.append, escalate=escalate)
            except Exception as exc:                         # noqa: BLE001
                out = {"closed": False, "error": str(exc)}
                journal.append({"event": "sleeve_error", "stage": "exit",
                                "error": str(exc),
                                "reason": "the position is STILL OPEN; the "
                                          "resting stop is the remaining "
                                          "protection until the next cycle"})
            journal.append({"event": "sleeve_exit_done", **out})
            if out.get("closed"):
                sleeve_live = None
                retired = C.SLEEVE_ONE_SHOT
        elif not do_exit and sleeve_live.stop_order_id is None and not dry:
            # The protection failed to place when the position was opened, so
            # try again. A position that stays unprotected because nobody
            # looked twice is the failure worth engineering against.
            sid = place_sleeve_stop(broker, sleeve_live.underlying,
                                    sleeve_live.shares, sleeve_live.stop_price,
                                    f"{sleeve_base_id(now_et)}-s",
                                    journal.append)
            if sid:
                sleeve_live = replace(sleeve_live, stop_order_id=sid)

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
        P.save_sleeve(sleeve_live, sleeve_panel, retired=retired)
        return CycleResult(phase.mode, phase.reason, [], [], exits,
                           sleeve_panel)

    # --- the advisory layer. It can only shrink what follows: blackouts add
    # --- veto windows, the multiplier scales sizing DOWN, the cutoff moves
    # --- the entry deadline EARLIER. Nothing here can widen a limit.
    # The advisory layer no longer SIZES. Sixteen consecutive regime calls on
    # 2026-08-31 returned a multiplier of exactly 0.5 with mutually
    # contradictory prose attached -- the model was anchoring on a number and
    # narrating afterwards, so half the book was sized by an artifact. It
    # keeps the jobs it demonstrably does: naming event windows, vetoing a
    # structure, and standing the whole book down. Size comes from `regime.py`.
    llm_mult = 1.0
    brain_floor = 1.0
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
        llm_mult = min(adv_b.multiplier, adv_r.multiplier, 1.0)
        # ... but the ABSENT-brain tier survives, because it is not the model's
        # judgement. `source == "degraded"` means no provider is configured at
        # all; a provider that answers never sets it, so the anchored 0.5 that
        # caused this refactor cannot come back in through here.
        if adv_b.source == "degraded":
            brain_floor = C.DEGRADED_BRAIN_SIZE
        if adv_r.no_new_entries_after is not None:
            llm_cutoff = (min(llm_cutoff, adv_r.no_new_entries_after)
                          if llm_cutoff else adv_r.no_new_entries_after)
        journal.append({"event": "mind", "brain": mind.brain,
                        "blackouts": adv_b.source,
                        "regime": adv_r.source, "multiplier": llm_mult,
                        "brain_floor": brain_floor,
                        "multiplier_role": "stand-down only -- sizing is regime.py",
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
    # Set when an unbalanced fill makes book risk uncomputable. The sleeve
    # reads it too: "we do not know what we are holding" is not a state to
    # add a directional position on top of, whatever the trend says.
    halt_entries = False

    # A zero multiplier is the advisory layer standing the agent down, not the
    # chain being unreadable. Without this the fail-closed path reports
    # "could not assemble a valid structure from the chain" for every name --
    # a brain outage reads in the journal as a market-data problem.
    if llm_mult == 0.0:
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
        sleeve_panel["decision"] = "NO_TRADE"
        sleeve_panel["reason"] = reason
        journal.append({"event": "sleeve_decision", "decision": "NO_TRADE",
                        "reason": reason})
        P.save_sleeve(sleeve_live, sleeve_panel, retired=retired)
        return CycleResult(phase.mode, phase.reason, measurements, decisions,
                           exits, sleeve_panel)

    for und in C.UNIVERSE:
        got = measure(und)
        if got is None:
            decisions.append({"underlying": und, "decision": "NO_TRADE",
                              "reason": "chain not measurable"})
            journal.append({"event": "decision", **decisions[-1]})
            continue
        m, legs = got
        measurements.append(m.as_dict())

        reg = trend(und)
        journal.append({"event": "regime", **reg.as_dict()})
        # Stamped onto every decision below, not just the ones that trade. A
        # NO_TRADE row whose weight was 0.5 was refused on a HALF-SIZE book,
        # and the reader cannot reconstruct that from the contracts count of
        # a trade that never happened.
        rd = {"regime_weight": reg.weight, "regime_source": reg.source}

        structure, why = select.choose_structure(m)
        if structure == "NO_TRADE":
            decisions.append({"underlying": und, "decision": "NO_TRADE",
                              "reason": why, **m.as_dict(), **rd})
            journal.append({"event": "decision", **decisions[-1]})
            continue

        # A measured stand-down is a decision, not an unreadable chain. Without
        # this the weight-0 path falls into the generic "could not assemble"
        # branch below -- the exact misattribution this file already fixes 45
        # lines up for the LLM stand-down, reintroduced for the sizer.
        if reg.weight == 0.0:
            decisions.append({"underlying": und, "decision": "NO_TRADE",
                              "reason": f"STAND_DOWN: {reg.notes}",
                              **m.as_dict(), **rd})
            journal.append({"event": "decision", **decisions[-1]})
            continue

        prof = traded(und) if C.PROFILE_ENABLED else VP.degraded(und, "disabled")
        journal.append({"event": "profile", **prof.as_dict()})
        # The profile can WEAKEN the structure the skew map chose: if every
        # in-band call strike sits inside the traded value area, the call side
        # is dropped and a CONDOR becomes a put spread. Everything downstream
        # -- sizing, gates, the journal -- must use what came back, not what
        # was asked for, or the book records a condor it does not hold.
        sided, structure, snote = S.assemble(structure, legs, und, prof)
        if snote and snote != "assembled as requested":
            rd["profile_note"] = snote
            journal.append({"event": "profile_filter", "underlying": und,
                            "effective_structure": structure, "note": snote})
        # The weight is applied to the NAV used for SIZING only, never to a
        # risk threshold, and it is bounded at 1.0 -- it can only shrink the
        # book. A weight of 0.0 yields zero contracts and the name is skipped,
        # which is the intended "stand down" behaviour. Every gate still runs
        # against the result: G3 caps per-position and book risk regardless of
        # what any regime says.
        # MIN, not product. Both terms are floors meaning "information is
        # missing, take less risk" -- the measured trend, and the absent brain.
        # Multiplying two independent 0.5s gives 0.25, which sizes zero
        # contracts and silently converts a sizing policy into a stand-down
        # neither tier intended. The binding floor governs.
        cand = (S.build(und, structure, sided, nav * min(reg.weight, brain_floor))
                if sided else None)
        if cand is None:
            decisions.append({"underlying": und, "decision": "NO_TRADE",
                              "reason": f"{structure}: could not assemble a "
                                        f"valid structure from the chain",
                              **rd})
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
            "reason": why, "gates": reasons, **m.as_dict(), **rd,
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
                halt_entries = True
                break

    # --- the sleeve, entered LAST. The options book is the thesis and gets
    # --- first call on the cycle; the sleeve is additive and cannot starve it
    # --- because its risk comes out of its own carve-out, not G3's ramp.
    if sleeve_live is None and retired:
        sd = {"decision": "NO_TRADE",
              "reason": ("RETIRED: the sleeve has had its one entry. The 1.2% "
                         "carve-out funds exactly one stop loss, and a stop "
                         "that re-buys is not a stop.")}
        sleeve_panel |= sd
        journal.append({"event": "sleeve_decision", **sd})
    elif sleeve_live is None:
        reg_s = trend(C.SLEEVE_UNDERLYING)
        journal.append({"event": "sleeve_regime", **reg_s.as_dict()})
        spx = sleeve_spot()
        cand_s = (SL.size(C.SLEEVE_UNDERLYING, spx, reg_s, floor=brain_floor)
                  if spx is not None else None)
        sd: dict[str, Any] = {"regime_weight": reg_s.lrs_weight,
                              "regime_source": reg_s.source,
                              "spot": round(spx, 2) if spx is not None else None}
        if halt_entries:
            sd |= {"decision": "NO_TRADE",
                   "reason": "entries halted: an unbalanced options fill left "
                             "book risk uncomputable this cycle"}
        elif cand_s is None:
            sd |= {"decision": "NO_TRADE",
                   "reason": (f"no quote for {C.SLEEVE_UNDERLYING}"
                              if spx is None else
                              f"LRS weight {reg_s.lrs_weight:.2f} below the "
                              f"{C.SLEEVE_MIN_LRS_W} ladder rung -- "
                              f"{reg_s.notes}"
                              if reg_s.lrs_weight < C.SLEEVE_MIN_LRS_W else
                              f"${C.SLEEVE_NOTIONAL:,.0f} ceiling at weight "
                              f"{min(reg_s.lrs_weight, brain_floor):.2f} does "
                              f"not buy one share at {spx:.2f}")}
        else:
            base = sleeve_base_id(now_et)
            ctx_s = Context(now_et=now_et, halt_file_present=halted,
                            blackouts=tuple(blackouts),
                            llm_no_new_entries_after=llm_cutoff,
                            client_order_id=f"{base}-e")
            allowed, s_reasons = SL.evaluate(cand_s, book, reg_s, ctx_s)
            sd |= {"decision": "LONG" if allowed else "VETOED",
                   "reason": cand_s.notes, "gates": s_reasons,
                   **cand_s.as_dict()}
            if allowed and not dry:
                # The sleeve is ADDITIVE. The options book is the submission's
                # thesis and has already done its work by the time we get
                # here, so a broker fault on a share of QQQ must not be able
                # to throw away a cycle that measured, gated and filled
                # condors. Loud in the journal, contained in scope.
                try:
                    rec_s = submit_sleeve_entry(broker, cand_s, base,
                                                journal.append)
                except Exception as exc:                     # noqa: BLE001
                    rec_s = {"filled_qty": 0}
                    sd["decision"] = "ERROR"
                    sd["reason"] = f"sleeve entry failed: {exc}"
                    journal.append({"event": "sleeve_error", "stage": "entry",
                                    "error": str(exc)})
                if rec_s["filled_qty"] > 0:
                    sleeve_live = SL.SleevePosition(
                        underlying=cand_s.underlying,
                        shares=int(rec_s["filled_qty"]),
                        entry_price=float(rec_s["fill_price"]),
                        stop_price=float(rec_s["stop_price"]),
                        opened_at=now_et, order_id=str(rec_s["order_id"]),
                        stop_order_id=rec_s["stop_order_id"])
                    journal.append({"event": "sleeve_opened",
                                    "underlying": sleeve_live.underlying,
                                    "shares": sleeve_live.shares,
                                    "entry_price": sleeve_live.entry_price,
                                    "stop_price": sleeve_live.stop_price,
                                    "notional": round(sleeve_live.notional, 2),
                                    "stop_resting":
                                        sleeve_live.stop_order_id is not None})
        sleeve_panel |= sd
        journal.append({"event": "sleeve_decision", **sd})

    journal.append({"event": "cycle_end", "cycle": cycle, "mode": phase.mode,
                    "entries": len(opened)})
    state.heartbeat(cycle, phase.mode, phase.reason,
                    {"nav": nav, "entries": len(opened),
                     "brain": mind.brain if mind else "none"})
    state.write("surface", measurements)
    state.write("decisions", decisions)
    # Only when something was actually measured. Writing an empty list on a
    # cycle that stood down before the entry loop would blank the panel and
    # stamp `written_at`, reporting "no sizing published" as though it were
    # fresh; leaving the last real measurement in place ages honestly instead.
    if regimes:
        state.write("regime", [regimes[u].as_dict()
                               for u in C.UNIVERSE if u in regimes])
    P.save_sleeve(sleeve_live, sleeve_panel, retired=retired)
    return CycleResult(phase.mode, phase.reason, measurements, decisions,
                       exits, sleeve_panel)

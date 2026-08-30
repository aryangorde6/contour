"""The seven gate cases the spec requires, plus the journal chain.

These are written before any strategy code exists. If a gate is wrong, the
agent trades wrong on a judged account for four days.
"""
from __future__ import annotations

from datetime import date, datetime

import pytest

from contour import config as C
from contour import gates
from contour.journal import Journal
from contour.models import Blackout, Book, Candidate, Context, Leg, OpenPosition

ET = C.ET


def leg(side="sell", strike=752.0, delta=-0.13, iv=0.145, oi=5000,
        exp=None, bid=1.00, ask=1.05, tradable=True, close=1.02, otype="put"):
    return Leg(
        symbol=f"SPY260911P00{int(strike)}000", side=side, ratio_qty=1,
        option_type=otype, strike=strike, expiration_date=exp or C.EXPIRY,
        bid=bid, ask=ask, delta=delta, implied_volatility=iv,
        open_interest=oi, tradable=tradable, close_price=close,
    )


def put_spread(**over):
    """13-delta short put, 6-delta long wing $5 below. ~$0.90 credit."""
    legs = (
        leg(side="sell", strike=752.0, delta=-0.13),
        leg(side="buy", strike=747.0, delta=-0.06, bid=0.10, ask=0.14, close=0.12),
    )
    base = dict(underlying="SPY", structure="PUT_CS", legs=legs,
                net_credit=0.90, wing_width=5.0, contracts=2,
                max_loss_per_contract=410.0)
    base.update(over)
    return Candidate(**base)


def ctx(when=datetime(2026, 8, 31, 11, 0, tzinfo=ET), **over):
    base = dict(now_et=when, client_order_id="contour-abc123-r1")
    base.update(over)
    return Context(**base)


def book(nav=100_000.0, pnl=0.0, positions=()):
    return Book(nav=nav, session_pnl=pnl, positions=positions)


# --- 1. NAV floor ---------------------------------------------------------
def test_g1_blocks_below_capital_floor():
    ok, why = gates.g1_capital_floor(put_spread(), book(nav=96_500), ctx())
    assert not ok and "no new entries" in why

    ok, why = gates.g1_capital_floor(put_spread(), book(nav=95_000), ctx())
    assert not ok and "HARD HALT" in why

    ok, _ = gates.g1_capital_floor(put_spread(), book(nav=99_000), ctx())
    assert ok


# --- 2. book risk ramp ----------------------------------------------------
def test_g3_ramp_is_tighter_on_monday_than_wednesday():
    """820 of new risk on top of 1200 open = 2020. Monday's cap is 2000."""
    open_pos = (OpenPosition("QQQ", "CONDOR", 3, 400.0, 0.95),)  # 1200
    cand = put_spread(contracts=2, max_loss_per_contract=410.0)  # 820

    mon = ctx(datetime(2026, 8, 31, 11, 0, tzinfo=ET))
    ok, why = gates.g3_book_risk_ramp(cand, book(positions=open_pos), mon)
    assert not ok and "ramp cap $2,000" in why

    wed = ctx(datetime(2026, 9, 2, 11, 0, tzinfo=ET))
    ok, _ = gates.g3_book_risk_ramp(cand, book(positions=open_pos), wed)
    assert ok, "8% Wednesday ramp should admit the same position"


def test_g3_rejects_position_over_one_percent_nav():
    cand = put_spread(contracts=3, max_loss_per_contract=410.0)  # 1230 > 1000
    ok, why = gates.g3_book_risk_ramp(cand, book(), ctx())
    assert not ok and "1.0% NAV cap" in why


# --- 3. null-Greek veto ---------------------------------------------------
def test_g6_null_greek_is_a_hard_veto_never_zero():
    """0DTE contracts return null Greeks. Coercing to 0.0 would sail through
    the delta band as a 'perfectly neutral' leg. It must veto instead."""
    legs = (leg(side="sell", delta=None), leg(side="buy", strike=747.0, delta=-0.06))
    ok, why = gates.g6_greeks_validity(put_spread(legs=legs), book(), ctx())
    assert not ok and "delta is null" in why

    legs = (leg(side="sell", iv=None), leg(side="buy", strike=747.0, delta=-0.06))
    ok, why = gates.g6_greeks_validity(put_spread(legs=legs), book(), ctx())
    assert not ok and "IV is null" in why


# --- 4. delta band --------------------------------------------------------
def test_g7_rejects_short_leg_outside_band():
    fat = (leg(side="sell", delta=-0.28), leg(side="buy", strike=747.0, delta=-0.06))
    ok, why = gates.g7_delta_band(put_spread(legs=fat), book(), ctx())
    assert not ok and "outside [0.1, 0.16]" in why.replace("0.10", "0.1")

    ok, _ = gates.g7_delta_band(put_spread(), book(), ctx())
    assert ok


# --- 5. expiry lock -------------------------------------------------------
def test_g8_rejects_anything_not_sep_11():
    """Sep 4 expiry contains NFP and would expire inside the judged window."""
    bad = (leg(side="sell", exp=date(2026, 9, 4)),
           leg(side="buy", strike=747.0, delta=-0.06, exp=date(2026, 9, 4)))
    ok, why = gates.g8_expiry_lock(put_spread(legs=bad), book(), ctx())
    assert not ok and "2026-09-11" in why

    ok, _ = gates.g8_expiry_lock(put_spread(), book(), ctx())
    assert ok


# --- 6. event blackout ----------------------------------------------------
def test_g10_blocks_inside_llm_and_fallback_windows():
    window = Blackout(
        start=datetime(2026, 9, 1, 9, 55, tzinfo=ET),
        end=datetime(2026, 9, 1, 10, 25, tzinfo=ET),
        reason="ISM Manufacturing 10:00 ET",
    )
    inside = ctx(datetime(2026, 9, 1, 10, 5, tzinfo=ET), blackouts=(window,))
    ok, why = gates.g10_event_blackout(put_spread(), book(), inside)
    assert not ok and "ISM Manufacturing" in why

    # fallback table fires even with no LLM output at all
    bare = ctx(datetime(2026, 9, 1, 10, 5, tzinfo=ET))
    ok, why = gates.g10_event_blackout(put_spread(), book(), bare)
    assert not ok and "fallback" in why

    clear = ctx(datetime(2026, 9, 1, 13, 0, tzinfo=ET))
    ok, _ = gates.g10_event_blackout(put_spread(), book(), clear)
    assert ok


# --- 7. HALT file + idempotency ------------------------------------------
def test_g12_halt_file_and_duplicate_order_id():
    ok, why = gates.g12_kill_switch(put_spread(), book(), ctx(halt_file_present=True))
    assert not ok and "HALT" in why

    dup = ctx(seen_client_order_ids=frozenset({"contour-abc123-r1"}))
    ok, why = gates.g12_kill_switch(put_spread(), book(), dup)
    assert not ok and "already used" in why

    ok, _ = gates.g12_kill_switch(put_spread(), book(), ctx())
    assert ok


# --- schedule: Friday is verify-only -------------------------------------
def test_g11_friday_is_verify_only_and_thursday_cuts_off_at_11():
    fri = ctx(datetime(2026, 9, 4, 10, 30, tzinfo=ET))
    ok, why = gates.g11_schedule(put_spread(), book(), fri)
    assert not ok and "VERIFY_ONLY" in why

    thu_late = ctx(datetime(2026, 9, 3, 13, 0, tzinfo=ET))
    ok, why = gates.g11_schedule(put_spread(), book(), thu_late)
    assert not ok and "after Thu" in why

    thu_ok = ctx(datetime(2026, 9, 3, 10, 30, tzinfo=ET))
    ok, _ = gates.g11_schedule(put_spread(), book(), thu_ok)
    assert ok

    too_early = ctx(datetime(2026, 8, 31, 9, 45, tzinfo=ET))
    ok, why = gates.g11_schedule(put_spread(), book(), too_early)
    assert not ok and "before entry open" in why


# --- liquidity must admit a normal cheap wing ----------------------------
def test_g5_admits_a_normal_cheap_wing_but_rejects_a_bad_market():
    """$0.10/$0.14 on a 6-delta wing is 4c wide and perfectly tradable, even
    though it is 33% of mid. A flat percentage test rejects the whole strategy."""
    ok, why = gates.g5_liquidity(put_spread(), book(), ctx())
    assert ok, why

    wide = (leg(side="sell", strike=752.0, delta=-0.13),
            leg(side="buy", strike=747.0, delta=-0.06, bid=0.05, ask=0.30, close=0.15))
    ok, why = gates.g5_liquidity(put_spread(legs=wide), book(), ctx())
    assert not ok and "exceeds both" in why

    illiquid = (leg(side="sell", strike=752.0, delta=-0.13, oi=100),
                leg(side="buy", strike=747.0, delta=-0.06, bid=0.10, ask=0.14, close=0.12))
    ok, why = gates.g5_liquidity(put_spread(legs=illiquid), book(), ctx())
    assert not ok and "OI 100" in why


def test_g5_friction_guard_kills_a_single_name_style_package():
    """The ETF-only decision exists because single-name weeklies cost $40-80
    round trip against a $30-42 edge. This gate enforces that, not assumes it."""
    single_name = (
        leg(side="sell", strike=752.0, delta=-0.13, bid=0.40, ask=0.50, close=0.45),
        leg(side="buy", strike=747.0, delta=-0.06, bid=0.10, ask=0.20, close=0.15),
    )
    cand = put_spread(legs=single_name, net_credit=0.30, wing_width=5.0)
    ok, why = gates.g5_liquidity(cand, book(), ctx())
    assert not ok and "round-trip friction" in why


# --- credit floor must admit what 13-delta actually pays -----------------
def test_g9_admits_real_13_delta_structures_and_rejects_thin_ones():
    """A flat 20%-of-wing floor rejected every structure in the spec's own
    table. These are the numbers the strategy will actually see."""
    spy_condor = put_spread(structure="CONDOR", net_credit=0.90, wing_width=5.0)
    ok, why = gates.g9_credit_floor(spy_condor, book(), ctx())
    assert ok, why

    vertical = put_spread(structure="PUT_CS", net_credit=0.45, wing_width=5.0)
    ok, why = gates.g9_credit_floor(vertical, book(), ctx())
    assert ok, why

    # The real SPY Sep-11 13-delta condor measured on 2026-08-30.
    measured = put_spread(structure="CONDOR", net_credit=0.870, wing_width=5.0)
    ok, why = gates.g9_credit_floor(measured, book(), ctx())
    assert ok, f"live-measured condor must pass: {why}"

    iwm = put_spread(structure="CONDOR", net_credit=0.42, wing_width=2.0)
    ok, why = gates.g9_credit_floor(iwm, book(), ctx())
    assert ok, why

    thin = put_spread(structure="PUT_CS", net_credit=0.20, wing_width=5.0)
    ok, why = gates.g9_credit_floor(thin, book(), ctx())
    assert not ok and "below" not in why and "floor" in why


# --- evaluate() short-circuits and journals every reason -----------------
def test_evaluate_stops_at_first_failure_and_reports_reasons():
    allowed, reasons = gates.evaluate(put_spread(), book(), ctx())
    assert allowed
    assert len(reasons) == 12, "a clean pass records all twelve reasons"

    allowed, reasons = gates.evaluate(put_spread(), book(nav=95_000), ctx())
    assert not allowed
    assert reasons[-1].startswith("G1"), "short-circuits at the first failing gate"


# --- journal hash chain ---------------------------------------------------
def test_journal_chain_detects_tampering(tmp_path):
    j = Journal(tmp_path / "2026-08-31.jsonl")
    j.append({"cycle": 1, "decision": "NO_TRADE", "reason": "G10 fallback blackout"})
    j.append({"cycle": 2, "decision": "OPEN", "order_id": "abc"})
    j.append({"cycle": 3, "decision": "CLOSE", "order_id": "def"})

    ok, msg = j.verify()
    assert ok and "3 records" in msg

    lines = (tmp_path / "2026-08-31.jsonl").read_text().splitlines()
    lines[1] = lines[1].replace('"order_id":"abc"', '"order_id":"zzz"')
    (tmp_path / "2026-08-31.jsonl").write_text("\n".join(lines) + "\n")

    ok, msg = j.verify()
    assert not ok and "tampered at seq 1" in msg

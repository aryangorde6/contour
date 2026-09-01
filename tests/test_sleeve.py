"""The directional sleeve: what sizes it, what stops it, what it costs.

The sleeve is the one part of this agent that can lose money in a straight
line, so the tests that matter most are not the happy path. They are:

  * the capital floor still holds once TWO books can reach max loss at once
  * the stop rests at the price we actually FILLED at, not the one we quoted
  * the resting stop is cancelled BEFORE the exit sells the same shares
  * a broken sleeve cannot take the options book down with it
"""
from __future__ import annotations

from datetime import datetime

import pytest

from contour import config as C
from contour import execute as E
from contour import loop as L
from contour import positions as P
from contour import sleeve as SL
from contour import state
from contour.journal import Journal
from contour.models import Book, Context
from contour.regime import Regime

from .test_loop import Broker, Src, StubMind, chain, measurement, patch_chains

ET = C.ET
TRADING_DAY = datetime(2026, 9, 2, 12, 0, tzinfo=ET)


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "ROOT", tmp_path)
    yield tmp_path


def reg(lrs=1.0, stage2=True, ribbon=True, source="measured"):
    return Regime(underlying=C.SLEEVE_UNDERLYING, stage2=stage2,
                  ribbon_bull=ribbon, lrs_weight=lrs,
                  weight=lrs if (stage2 and ribbon) else lrs * 0.5,
                  source=source, notes="test regime")


def book(nav=100_000.0, pnl=0.0):
    return Book(nav=nav, session_pnl=pnl)


def ctx(now=TRADING_DAY, **kw):
    kw.setdefault("client_order_id", "contour-sleeve-abc-e")
    return Context(now_et=now, **kw)


class QuoteSrc(Src):
    """`Src`, but it answers a bare quote.

    The sleeve holds shares, so on a cycle where nothing read the QQQ option
    chain it falls back to `ds.spot` rather than refusing to manage an equity
    position. That fallback is the path these tests exercise; the base class
    raises there precisely to keep the OPTIONS code on its measurement cache.
    """

    def __init__(self, spot=769.0, history="up"):
        super().__init__(history)
        self._spot = spot

    def spot(self, u):
        return self._spot


def position(shares=41, entry=717.0, stop=688.32, stop_id="s1"):
    return SL.SleevePosition(underlying=C.SLEEVE_UNDERLYING, shares=shares,
                             entry_price=entry, stop_price=stop,
                             opened_at=TRADING_DAY, order_id="o1",
                             stop_order_id=stop_id)


# --- 1. the capital floor, now that two books share it --------------------
def test_the_two_books_together_still_fit_behind_the_hard_halt():
    """The whole reason G3's ceiling was rewired. Before the sleeve, the
    options book could reach 4.0% and G1 halts at -4.0% -- exactly flush. Add
    a sleeve that can lose 1.2% ALONGSIDE that and the account can be 5.2%
    down with no gate having objected on the way. The ceiling is derived from
    the halt MINUS the sleeve, so that configuration cannot be built."""
    options_worst = max(C.BOOK_RISK_RAMP.values())
    both = options_worst + C.SLEEVE_RISK_BUDGET_PCT
    halt = (C.START_NAV - C.NAV_HARD_HALT) / C.START_NAV
    assert both <= halt, (
        f"options {options_worst:.3%} + sleeve {C.SLEEVE_RISK_BUDGET_PCT:.3%} "
        f"= {both:.3%} reachable behind a {halt:.3%} halt")


def test_the_sleeve_budget_is_what_the_sleeve_can_actually_spend():
    """A carve-out nobody can reach is decoration, and a carve-out the sleeve
    can overspend is worse than none. It must be exactly the ceiling."""
    assert (C.SLEEVE_RISK_BUDGET_PCT * C.START_NAV
            == pytest.approx(C.SLEEVE_NOTIONAL * C.SLEEVE_STOP_PCT))


def test_the_concentration_cap_is_derived_so_g3_never_has_to_refuse_a_full_name():
    """G4 must not hand G3 a book G3 is obliged to reject on the last
    position of every full underlying."""
    per_name = C.MAX_POSITIONS_PER_UNDERLYING * C.MAX_POSITION_RISK_PCT
    assert per_name <= C.BOOK_RISK_CEILING_PCT


def test_turning_the_sleeve_off_restores_the_options_book_exactly(monkeypatch):
    """The ceiling is derived, so `SLEEVE_NOTIONAL = 0` has to give the
    pre-sleeve numbers back rather than leaving the options book permanently
    shrunk by a feature nobody is running."""
    import importlib
    monkeypatch.setenv("PYTHONHASHSEED", "0")
    src = open(C.__file__).read().replace(
        "SLEEVE_NOTIONAL = 30_000.0", "SLEEVE_NOTIONAL = 0.0")
    ns: dict = {"__file__": C.__file__, "__name__": "contour._cfgprobe"}
    exec(compile(src, C.__file__, "exec"), ns)
    assert ns["BOOK_RISK_CEILING_PCT"] == pytest.approx(0.04)
    assert ns["MAX_POSITIONS_PER_UNDERLYING"] == 3


# --- 2. sizing ------------------------------------------------------------
def test_a_clean_ladder_deploys_the_whole_ceiling():
    cand = SL.size("QQQ", 717.01, reg(lrs=1.0))
    assert cand.shares == int(C.SLEEVE_NOTIONAL // 717.01) == 41
    assert cand.notional <= C.SLEEVE_NOTIONAL
    assert cand.stop_price == pytest.approx(717.01 * 0.96, abs=0.01)


def test_the_warning_rung_genuinely_halves_the_notional():
    """The LRS weight has to BIND. If entry required a perfect 1.0 the
    vol-scaling rule would be decoration -- the exact charge this project
    levelled at G3's old 8% ramp."""
    full = SL.size("QQQ", 717.01, reg(lrs=1.0))
    warn = SL.size("QQQ", 717.01, reg(lrs=0.5))
    assert warn is not None
    assert warn.shares == full.shares // 2
    assert warn.notional < 0.55 * C.SLEEVE_NOTIONAL


def test_below_the_ladder_rung_there_is_no_candidate_at_all():
    assert SL.size("QQQ", 717.01, reg(lrs=0.0)) is None
    assert SL.size("QQQ", 717.01, reg(lrs=0.4)) is None


def test_the_absent_brain_floor_is_a_min_not_a_product():
    """Both terms mean "take less risk". Multiplying a 0.5 ladder rung by a
    0.5 absent-brain floor gives 0.25 and silently converts a sizing policy
    into a near stand-down neither tier asked for."""
    cand = SL.size("QQQ", 717.01, reg(lrs=0.5), floor=0.5)
    assert cand.weight == 0.5
    assert cand.shares == int((C.SLEEVE_NOTIONAL * 0.5) // 717.01)

    # and the floor binds when it is the smaller of the two
    floored = SL.size("QQQ", 717.01, reg(lrs=1.0), floor=0.5)
    assert floored.weight == 0.5
    assert "floored to 0.50 by the absent brain" in floored.notes


def test_a_ceiling_that_cannot_buy_one_share_is_no_candidate():
    assert SL.size("QQQ", 717.01, reg(), notional_cap=500.0) is None


def test_the_modeled_max_loss_is_what_the_stop_actually_caps():
    cand = SL.size("QQQ", 717.01, reg())
    assert cand.modeled_max_loss == pytest.approx(
        cand.shares * cand.spot * C.SLEEVE_STOP_PCT, abs=1.0)


# --- 3. S1-S7 -------------------------------------------------------------
def cand():
    return SL.size("QQQ", 717.01, reg())


def test_s1_shares_the_options_capital_floor():
    ok, why = SL.s1_capital_floor(cand(), book(nav=95_000), reg(), ctx())
    assert not ok and "HARD HALT" in why
    ok, why = SL.s1_capital_floor(cand(), book(nav=96_500), reg(), ctx())
    assert not ok and "no new entries" in why
    assert SL.s1_capital_floor(cand(), book(), reg(), ctx())[0]


def test_s2_shares_the_daily_loss_halt():
    ok, why = SL.s2_daily_loss_halt(cand(), book(pnl=-2_000), reg(), ctx())
    assert not ok and "daily halt" in why


def test_s3_refuses_a_directional_bet_on_one_witness():
    """The options book trades at half size on a single confirmation. The
    sleeve does not trade at all: a leveraged long is not a position to open
    on one of two trend systems."""
    ok, why = SL.s3_trend_confirmation(cand(), book(), reg(ribbon=False), ctx())
    assert not ok and "ribbon" in why and "both confirmations" in why

    ok, why = SL.s3_trend_confirmation(cand(), book(), reg(stage2=False), ctx())
    assert not ok and "Stage-2" in why


def test_s3_refuses_a_regime_it_could_not_measure():
    """Degraded is not bullish. The options book degrades to half size on an
    unmeasurable regime; the sleeve must not open one on a guess."""
    ok, why = SL.s3_trend_confirmation(cand(), book(), reg(source="degraded"),
                                       ctx())
    assert not ok and "not measured" in why


def test_s4_caps_the_notional_at_the_ceiling():
    big = SL.SleeveCandidate("QQQ", 717.01, 100, 688.33, 1.0, "oversized")
    ok, why = SL.s4_notional_ceiling(big, book(), reg(), ctx())
    assert not ok and "exceeds ceiling" in why


def test_s5_refuses_a_position_that_would_overspend_the_carve_out():
    """If this could be beaten, the options book was shrunk to fund an
    allowance the sleeve then overspent."""
    wide = SL.SleeveCandidate("QQQ", 717.01, 41, 600.0, 1.0, "stop far away")
    assert wide.modeled_max_loss > C.SLEEVE_RISK_BUDGET_PCT * 100_000
    ok, why = SL.s5_risk_budget(wide, book(), reg(), ctx())
    assert not ok and "carve-out" in why


def test_s6_will_not_open_a_sleeve_it_is_about_to_have_to_flatten():
    late = datetime(2026, 9, 3, 15, 50, tzinfo=ET)
    ok, why = SL.s6_schedule(cand(), book(), reg(), ctx(now=late))
    assert not ok
    friday = datetime(2026, 9, 4, 11, 30, tzinfo=ET)
    ok, why = SL.s6_schedule(cand(), book(), reg(), ctx(now=friday))
    assert not ok and "VERIFY_ONLY" in why


def test_s7_reads_the_same_halt_file_as_g12():
    ok, why = SL.s7_kill_switch(cand(), book(), reg(),
                                ctx(halt_file_present=True))
    assert not ok and "HALT file" in why
    ok, why = SL.s7_kill_switch(cand(), book(), reg(),
                                ctx(seen_client_order_ids=frozenset(
                                    {"contour-sleeve-abc-e"})))
    assert not ok and "already used" in why


def test_evaluate_stops_at_the_first_refusal_and_reports_every_reason():
    allowed, reasons = SL.evaluate(cand(), book(), reg(), ctx())
    assert allowed and len(reasons) == len(SL.SLEEVE_GATES)
    assert all(r.startswith(f"S{i} ok") for i, r in enumerate(reasons, 1))

    allowed, reasons = SL.evaluate(cand(), book(nav=95_000), reg(), ctx())
    assert not allowed and len(reasons) == 1


# --- 4. exits -------------------------------------------------------------
def test_the_clock_beats_every_other_consideration():
    after = datetime(2026, 9, 3, 15, 46, tzinfo=ET)
    out, why = SL.should_exit(position(), 900.0, reg(), after)
    assert out and "FLATTEN" in why


def test_the_price_stop_fires_at_or_through_the_level():
    out, why = SL.should_exit(position(stop=688.32), 688.32, reg(), TRADING_DAY)
    assert out and "STOP" in why
    out, _ = SL.should_exit(position(stop=688.32), 688.33, reg(), TRADING_DAY)
    assert not out


def test_a_lost_confirmation_closes_the_position_that_confirmation_opened():
    out, why = SL.should_exit(position(), 720.0, reg(ribbon=False), TRADING_DAY)
    assert out and "TREND_BREAK" in why and "ribbon" in why


def test_falling_off_the_ladder_rung_is_an_exit_not_a_resize():
    """Fortress trims continuously. Over a four-day window each trim pays a
    spread to express a distinction the horizon cannot resolve, so the
    halving rung is treated as an exit -- strictly more conservative."""
    out, why = SL.should_exit(position(), 720.0, reg(lrs=0.0), TRADING_DAY)
    assert out and "ladder rung" in why


def test_an_unmeasurable_regime_does_not_close_a_position():
    """Same two-tier policy the brain and the sizer use: "we could not
    measure the trend" is not evidence the trend broke, and the resting stop
    already bounds the position."""
    out, why = SL.should_exit(position(), 720.0, reg(source="degraded"),
                              TRADING_DAY)
    assert not out and "HOLD" in why
    out, _ = SL.should_exit(position(), 720.0, None, TRADING_DAY)
    assert not out


# --- 5. execution ---------------------------------------------------------
class EquityBroker:
    """Records every call in order. The ORDER is what several of these tests
    are actually asserting on."""

    def __init__(self, fill_qty=41, fill_price=717.40, stop_raises=False):
        self.calls: list[tuple] = []
        self.fill_qty, self.fill_price = fill_qty, fill_price
        self.stop_raises = stop_raises
        self.canceled: list[str] = []

    def account(self):
        return {"equity": 100_000.0, "account_number": "TEST"}

    def submit_equity(self, symbol, qty, side, order_type, client_order_id,
                      limit_price=None, stop_price=None, tif="day",
                      dry_run=False):
        self.calls.append(("submit", side, order_type, qty, limit_price,
                           stop_price, tif, dry_run))
        if order_type == "stop":
            if self.stop_raises:
                raise E.BrokerError("stop rejected")
            return {"id": "stop-1", "status": "new"}
        return {"id": "entry-1", "status": "accepted"}

    def get_order(self, oid):
        self.calls.append(("get", oid))
        return {"id": oid, "status": "filled", "qty": "41",
                "filled_qty": str(self.fill_qty),
                "filled_avg_price": str(self.fill_price)}

    def cancel(self, oid):
        self.calls.append(("cancel", oid))
        self.canceled.append(oid)


def journal_to(recs):
    return recs.append


def test_the_stop_is_priced_off_the_fill_not_off_the_quote():
    """S5 approved a specific dollar loss. A stop 4% below a price we never
    traded at is not the stop the risk budget was derived from."""
    b = EquityBroker(fill_price=730.00)
    recs: list[dict] = []
    out = E.submit_sleeve_entry(b, cand(), "base", journal_to(recs),
                                wait_s=0, sleep=lambda s: None)
    assert out["stop_price"] == pytest.approx(730.00 * 0.96, abs=0.01)
    assert out["stop_price"] != pytest.approx(717.01 * 0.96, abs=0.01)


def test_a_partial_fill_is_protected_for_what_filled_not_what_was_asked():
    b = EquityBroker(fill_qty=20)
    recs: list[dict] = []
    out = E.submit_sleeve_entry(b, cand(), "base", journal_to(recs),
                                wait_s=0, sleep=lambda s: None)
    assert out["filled_qty"] == 20
    stop = next(c for c in b.calls if c[0] == "submit" and c[2] == "stop")
    assert stop[3] == 20, "the resting stop must cover exactly what filled"


def test_the_protective_stop_rests_gtc_because_the_agent_does_not():
    b = EquityBroker()
    E.submit_sleeve_entry(b, cand(), "base", [].append, wait_s=0,
                          sleep=lambda s: None)
    stop = next(c for c in b.calls if c[0] == "submit" and c[2] == "stop")
    assert stop[6] == "gtc", "a day-TIF stop expires before the gap it exists for"


def test_a_stop_that_will_not_place_is_loud_but_not_fatal():
    b = EquityBroker(stop_raises=True)
    recs: list[dict] = []
    out = E.submit_sleeve_entry(b, cand(), "base", journal_to(recs),
                                wait_s=0, sleep=lambda s: None)
    assert out["filled_qty"] > 0 and out["stop_order_id"] is None
    fail = next(r for r in recs if r["event"] == "sleeve_stop_failed")
    assert "UNPROTECTED" in fail["reason"]


def test_the_exit_cancels_the_resting_stop_before_it_sells():
    """Both orders want the same shares. Leave the stop working and either the
    sell is rejected, or both fill and the account is SHORT QQQ."""
    b = EquityBroker()
    E.close_sleeve(b, position(stop_id="stop-1"), 720.0, "base", [].append)
    order = [c for c in b.calls if c[0] in ("cancel", "submit")]
    assert order[0][0] == "cancel", f"sold before cancelling: {order}"
    assert order[1][1] == "sell"


def test_a_stop_that_already_filled_is_detected_rather_than_sold_twice():
    class Filled(EquityBroker):
        def cancel(self, oid):
            raise E.BrokerError("order already filled")

        def get_order(self, oid):
            return {"id": oid, "filled_qty": "41", "filled_avg_price": "688.30"}

    b = Filled()
    recs: list[dict] = []
    out = E.close_sleeve(b, position(stop_id="stop-1"), 690.0, "base",
                         journal_to(recs))
    assert out["closed"] and out["via"] == "resting_stop"
    assert not [c for c in b.calls if c[0] == "submit"], "sold shares it had already sold"


def test_the_flatten_escalates_the_limit_because_not_getting_out_is_worse():
    b = EquityBroker()
    E.close_sleeve(b, position(), 720.0, "base", [].append, escalate=True)
    sell = next(c for c in b.calls if c[0] == "submit" and c[1] == "sell")
    assert sell[4] == pytest.approx(720.0 * (1 - C.SLEEVE_EXIT_SLIP_ESCALATED),
                                    abs=0.01)


# --- 6. the cycle ---------------------------------------------------------
def cycle(tmp_path, **kw):
    j = Journal(tmp_path / "j.jsonl")
    kw.setdefault("ds", Src())
    kw.setdefault("broker", Broker())
    kw.setdefault("now_et", TRADING_DAY)
    kw.setdefault("market_open", True)
    kw.setdefault("dry", True)
    res = L.run_cycle(journal=j, **kw)
    return res, [r.payload for r in j.read()]


def test_the_cycle_publishes_the_sleeve_so_the_dashboard_can_show_it(
        isolated_state, monkeypatch):
    patch_chains(monkeypatch, {u: (measurement(u), chain()) for u in C.UNIVERSE})
    res, recs = cycle(isolated_state, mind=StubMind())

    assert res.sleeve["decision"] in {"LONG", "VETOED", "NO_TRADE"}
    assert any(r["event"] == "sleeve_decision" for r in recs)
    assert (isolated_state / "sleeve.json").exists()
    assert P.load_sleeve() is None, "a dry cycle must not write down a position"


def test_a_saved_sleeve_is_exit_checked_even_when_the_entry_window_is_shut(
        isolated_state, monkeypatch):
    """The whole reason exits run before the mode check. A MANAGE_ONLY cycle
    that skipped the sleeve would leave Thursday's flatten unable to reach
    it."""
    patch_chains(monkeypatch, {})
    closed = datetime(2026, 9, 2, 16, 30, tzinfo=ET)
    res, recs = cycle(isolated_state, mind=StubMind(), now_et=closed,
                      market_open=False, sleeve_position=position())
    assert res.mode != "TRADE"
    chk = next(r for r in recs if r["event"] == "sleeve_exit_check")
    assert chk["shares"] == 41


def test_a_brain_stand_down_stands_the_sleeve_down_too(isolated_state,
                                                       monkeypatch):
    """A stand-down is a stand-down. Opening a directional position while the
    options book was refused for a fail-closed brain would make the phrase
    meaningless."""
    class Zero(StubMind):
        def regime(self, day, vrp):
            from contour.mind import Advice
            return Advice((), 0.0, None, "failed_closed", "fail closed")

    patch_chains(monkeypatch, {u: (measurement(u), chain()) for u in C.UNIVERSE})
    res, recs = cycle(isolated_state, mind=Zero())
    assert res.sleeve["decision"] == "NO_TRADE"
    assert "STAND_DOWN" in res.sleeve["reason"]
    assert not any(r["event"] == "sleeve_regime" for r in recs)


def test_a_broken_sleeve_cannot_take_the_options_book_down_with_it(
        isolated_state, monkeypatch):
    """The options book is the submission's thesis and has already measured,
    gated and filled by the time the sleeve runs. A broker fault on a share of
    QQQ must not throw that away."""
    class NoEquity(Broker):
        def submit_equity(self, *a, **k):
            raise E.BrokerError("equity endpoint down")

    patch_chains(monkeypatch, {u: (measurement(u), chain()) for u in C.UNIVERSE})
    # The options ladder is stubbed to "no fill": this test is about what
    # survives a sleeve fault, not about the ladder, and a real one would
    # sleep through three 90-second rungs to prove nothing.
    monkeypatch.setattr(L, "submit_with_ladder",
                        lambda *a, **k: {"filled_qty": 0, "order_id": None})
    res, recs = cycle(isolated_state, mind=StubMind(), dry=False,
                      broker=NoEquity())
    assert res.decisions, "the options book lost its work to a sleeve fault"
    err = next(r for r in recs if r["event"] == "sleeve_error")
    assert err["stage"] == "entry"
    assert any(r["event"] == "cycle_end" for r in recs)


def test_the_sleeve_position_survives_the_container_it_was_opened_in(
        isolated_state):
    """Every cycle is a fresh container. A sleeve that is not written down is
    an unmanaged directional position -- which is worse than an unmanaged
    condor, because it has no defined risk at all."""
    P.save_sleeve(position(), {"decision": "LONG"})
    back = P.load_sleeve()
    assert back == position()
    P.save_sleeve(None, {"decision": "STOPPED_OUT"})
    assert P.load_sleeve() is None


def test_the_thursday_flatten_actually_reaches_the_sleeve(isolated_state,
                                                          monkeypatch):
    """The flatten cron fires at 15:50 ET, which resolves to MANAGE_ONLY --
    the entry window shut half an hour earlier. If the sleeve's exit check sat
    below that mode check it would never run, and the submission's claim that
    the account is flat before the deadline would be false about the one
    position with directional risk."""
    sold: list = []

    class Selling(Broker):
        def submit_equity(self, symbol, qty, side, order_type,
                          client_order_id, **kw):
            sold.append((symbol, qty, side))
            return {"id": "x1", "status": "accepted"}

        def cancel(self, oid):
            sold.append(("cancel", oid, None))

    patch_chains(monkeypatch, {})
    flat_at = datetime(2026, 9, 3, 15, 50, tzinfo=ET)
    res, recs = cycle(isolated_state, mind=StubMind(), now_et=flat_at,
                      dry=False, broker=Selling(), sleeve_position=position())

    assert res.mode != "TRADE", "this test is meaningless on a TRADE cycle"
    chk = next(r for r in recs if r["event"] == "sleeve_exit_check")
    assert chk["exit"] and "FLATTEN" in chk["reason"]
    assert ("cancel", "s1", None) in sold, "sold without cancelling the stop"
    assert (C.SLEEVE_UNDERLYING, 41, "sell") in sold
    assert P.load_sleeve() is None, "the flattened sleeve is still on the book"


def test_a_stop_that_fired_overnight_is_not_managed_as_though_it_were_open(
        isolated_state, tmp_path):
    """The whole point of a GTC stop is that it works while nothing is
    watching. Trusting the saved file afterwards would have Thursday's flatten
    try to SELL shares the account no longer owns."""
    from contour.__main__ import _reconcile_sleeve

    class Flat:
        def positions(self):
            return [{"symbol": "SPY260911P00749000", "qty": "3",
                     "asset_class": "us_option"}]

    j = Journal(tmp_path / "j.jsonl")
    assert _reconcile_sleeve(Flat(), position(), j) is None
    rec = next(r.payload for r in j.read()
               if r.payload["event"] == "sleeve_stopped_out")
    assert "outside a cycle" in rec["reason"]

    class Partial:
        def positions(self):
            return [{"symbol": "QQQ", "qty": "20", "asset_class": "us_equity"}]

    back = _reconcile_sleeve(Partial(), position(), Journal(tmp_path / "k.jsonl"))
    assert back.shares == 20, "the broker's count is the authority, not ours"

    class Broken:
        def positions(self):
            raise RuntimeError("broker unreachable")

    # Unknown is not "gone": keep managing what we wrote down.
    assert _reconcile_sleeve(Broken(), position(),
                             Journal(tmp_path / "m.jsonl")) == position()


# --- 7. one entry, and only one ------------------------------------------
def test_a_stop_that_fires_is_not_re_bought_in_the_same_cycle(isolated_state,
                                                              monkeypatch):
    """The carve-out G3's ramp was reduced by funds exactly ONE stop loss. The
    exit block runs before the entry block, so without this the sleeve is
    stopped out at 11:00 and bought back at 11:00 -- spending 2.4% behind a 4%
    halt that also has to cover a 2.8% options book. A stop that immediately
    re-buys is not a stop."""
    class Filling(Broker):
        def submit_equity(self, symbol, qty, side, order_type,
                          client_order_id, **kw):
            return {"id": f"{side}-1", "status": "accepted"}

        def get_order(self, oid):
            return {"id": oid, "status": "filled", "qty": "41",
                    "filled_qty": "41", "filled_avg_price": "700.00"}

        def cancel(self, oid):
            pass

    # spot below the stop -> the price stop fires on this cycle
    patch_chains(monkeypatch, {u: (measurement(u), chain()) for u in C.UNIVERSE})
    monkeypatch.setattr(L, "submit_with_ladder",
                        lambda *a, **k: {"filled_qty": 0, "order_id": None})
    res, recs = cycle(isolated_state, mind=StubMind(), dry=False,
                      ds=QuoteSrc(spot=769.0), broker=Filling(),
                      sleeve_position=position(stop=800.0))

    chk = next(r for r in recs if r["event"] == "sleeve_exit_check")
    assert chk["exit"] and "STOP" in chk["reason"]
    assert res.sleeve["decision"] == "NO_TRADE"
    assert "RETIRED" in res.sleeve["reason"]
    assert not any(r["event"] == "sleeve_opened" for r in recs)
    assert P.sleeve_retired(), "the one-shot flag did not survive the cycle"


def test_a_retired_sleeve_stays_retired_on_later_cycles(isolated_state,
                                                        monkeypatch):
    patch_chains(monkeypatch, {u: (measurement(u), chain()) for u in C.UNIVERSE})
    res, recs = cycle(isolated_state, mind=StubMind(), sleeve_retired=True)
    assert res.sleeve["decision"] == "NO_TRADE"
    assert "one entry" in res.sleeve["reason"]
    # and it never even asked the trend systems -- there is nothing to size
    assert not any(r["event"] == "sleeve_regime" for r in recs)


def test_a_corrupt_sleeve_file_does_not_silently_retire_an_untraded_sleeve(
        isolated_state):
    assert P.sleeve_retired() is False, "a missing file is not a spent carve-out"
    (isolated_state / "sleeve.json").write_text("{not json")
    assert P.sleeve_retired() is False
    P.save_sleeve(None, {}, retired=True)
    assert P.sleeve_retired() is True

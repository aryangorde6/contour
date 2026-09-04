"""What the cycle actually passes to the things it calls.

Every bug in this file's history has the same shape: a function was correct,
fully unit-tested, and reached with the wrong argument -- or never reached at
all. `open_positions` defaulted to `()` and nothing passed it, so every exit
rule was dead code. `cycle` defaulted to 0 and nothing passed it, so the
published journal numbered every cycle 0 for a week. `regime()` was handed an
empty dict, so the model that sizes the whole book was asked to judge the vol
premium without being shown any.

Tests that call the callee directly cannot see any of that. These call the
cycle.
"""
from __future__ import annotations

import inspect
import json
from datetime import date, datetime

import pytest

from contour import config as C
from contour import loop as L
from contour import positions as P
from contour import state
from contour.journal import Journal
from contour.replay import ReplayError
from contour.mind import Advice, Verdict
from contour.models import Blackout, Measurement

from .test_gates import leg
from .test_manage import pos

ET = C.ET
# Dated to a session whose ramp still admits risk. The ramp closes to zero
# from 2026-09-02 (see config), and these tests are about fill handling
# rather than calendar policy -- pinning them to a closed day would test
# the ramp twice and the thing they name not at all.
TRADING_DAY = datetime(2026, 9, 1, 12, 0, tzinfo=ET)


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "ROOT", tmp_path)
    yield tmp_path


class Broker:
    def account(self):
        return {"equity": 100_000.0, "account_number": "TEST"}


class Src:
    """The option chain is patched; the daily history the regime reads is not.

    `closes` is answered for real because the cycle now sizes from it. Pass
    `history=None` to model a source that cannot serve it -- a fixture
    recorded before `regime.py` existed does exactly that.
    """

    def __init__(self, history="up"):
        self.history, self.closes_asked = history, []

    def spot(self, u):
        raise AssertionError("the cycle went round the measurement cache")

    def closes(self, u, n=11):
        self.closes_asked.append((u, n))
        if self.history is None:
            raise ReplayError(f"fixture has no closes for {u!r}|{n}")
        drift = 0.0006 if self.history == "up" else -0.0006
        px, out = 100.0, []
        for _ in range(n):
            px *= 1.0 + drift
            out.append(px)
        return out

    def legs(self, u, expiry, spot):
        raise AssertionError("the cycle went round the measurement cache")


class StubMind:
    """Records what the cycle hands it. That is the whole point."""

    brain = "stub:model"

    def __init__(self, windows=()):
        self.windows = tuple(windows)
        self.regime_saw: dict | None = None
        self.blackout_days: list[date] = []

    def blackouts(self, day, headlines=()):
        self.blackout_days.append(day)
        return Advice(self.windows, 1.0, None, "llm", "planned")

    def regime(self, day, vrp):
        self.regime_saw = dict(vrp)
        return Advice((), 1.0, None, "llm", "carry on")

    def confirm(self, *a, **k):
        return Verdict(veto=False, reason="fine")


# The same quotes test_manage builds its condor from: a real spread, so the
# structure carries a positive credit and actually reaches the gates.
QUOTES = ((749.0, -0.13, "put", 1.27, 1.32), (744.0, -0.06, "put", 0.95, 0.96),
          (785.0, 0.13, "call", 0.85, 0.90), (790.0, 0.06, "call", 0.34, 0.35))


def chain(strikes=QUOTES):
    legs = [leg(side="buy", strike=k, delta=d, otype=t, bid=b, ask=a)
            for k, d, t, b, a in strikes]
    for l in legs:
        object.__setattr__(l, "quote_age_s", 30.0)
    return legs


def measurement(und="SPY", vrp=1.45, skew_z=0.0):
    return Measurement(underlying=und, spot=769.0, atm_iv=11.4, rv10=7.8,
                       vrp_ratio=vrp, skew25=2.52, skew_z=skew_z)


def patch_chains(monkeypatch, table):
    """table: underlying -> (Measurement, legs) or None. Counts the calls."""
    calls: list[str] = []

    def fake(ds, und, expiry):
        calls.append(und)
        return table.get(und)

    monkeypatch.setattr(L, "measure_underlying", fake)
    return calls


def cycle(tmp_path, **kw):
    j = Journal(tmp_path / "j.jsonl")
    kw.setdefault("ds", Src())
    kw.setdefault("broker", Broker())
    kw.setdefault("now_et", TRADING_DAY)
    kw.setdefault("market_open", True)
    kw.setdefault("dry", True)
    res = L.run_cycle(journal=j, **kw)
    return res, j, [r.payload for r in j.read()]


# --- the regime call must see the surface it is sizing --------------------
def test_the_regime_call_is_shown_the_measured_vol_premium(isolated_state):
    """It was handed `{}`. The prompt then read "ratios right now: ." and the
    model sized the book off its prior instead of the market."""
    mind = StubMind()
    with pytest.MonkeyPatch().context() as mp:
        patch_chains(mp, {"SPY": (measurement("SPY", vrp=1.45), chain()),
                          "QQQ": (measurement("QQQ", vrp=1.21), chain()),
                          "IWM": None})
        cycle(isolated_state, mind=mind)

    assert mind.regime_saw == {"SPY": 1.45, "QQQ": 1.21}, (
        "the regime call did not see the surface")


def test_an_unmeasurable_name_is_omitted_rather_than_invented(isolated_state):
    mind = StubMind()
    with pytest.MonkeyPatch().context() as mp:
        patch_chains(mp, {"SPY": (measurement("SPY"), chain())})
        cycle(isolated_state, mind=mind)
    assert mind.regime_saw == {"SPY": 1.45}
    assert "QQQ" not in mind.regime_saw


def test_the_vol_premium_reaches_the_journal(isolated_state):
    mind = StubMind()
    with pytest.MonkeyPatch().context() as mp:
        patch_chains(mp, {"SPY": (measurement("SPY"), chain())})
        _, _, recs = cycle(isolated_state, mind=mind)
    rec = next(r for r in recs if r["event"] == "mind")
    assert rec["vrp"] == {"SPY": 1.45}


# --- one chain read per underlying per cycle ------------------------------
def test_the_chain_is_read_once_per_underlying_not_once_per_caller(
        isolated_state, monkeypatch):
    """The exit check, the regime call and the entry loop all ask the same
    question. Three round trips can return three different answers inside one
    cycle, and then the book is sized against numbers it never published."""
    P.save([pos(credit=0.87)])
    calls = patch_chains(monkeypatch,
                         {u: (measurement(u), chain()) for u in C.UNIVERSE})
    cycle(isolated_state, mind=StubMind(), open_positions=P.load())

    assert sorted(calls) == sorted(C.UNIVERSE), (
        f"expected one read per underlying, got {calls}")


# --- the cycle ordinal ----------------------------------------------------
def test_the_cycle_ordinal_survives_the_container(isolated_state):
    """Nothing survives a cron run except what was written down, so the count
    has to come off the last heartbeat."""
    assert state.next_cycle() == 1
    state.heartbeat(state.next_cycle(), "TRADE", "first")
    assert state.next_cycle() == 2
    state.heartbeat(state.next_cycle(), "TRADE", "second")
    assert state.next_cycle() == 3


def test_a_corrupt_heartbeat_restarts_the_count_instead_of_raising(
        isolated_state):
    (isolated_state / "heartbeat.json").write_text("{not json")
    assert state.next_cycle() == 1


def test_the_ordinal_reaches_the_journal_and_the_heartbeat(isolated_state,
                                                           monkeypatch):
    patch_chains(monkeypatch, {})
    _, _, recs = cycle(isolated_state, cycle=7)
    assert next(r for r in recs if r["event"] == "cycle_start")["cycle"] == 7
    hb = json.loads((isolated_state / "heartbeat.json").read_text())
    assert hb["cycle_count"] == 7


def test_the_entrypoint_actually_passes_the_ordinal():
    """The bug class this whole file exists for: a defaulted argument nobody
    passes is invisible to every test that calls the callee directly. There is
    no way to reach `main()` without live credentials, so pin the call site."""
    src = inspect.getsource(__import__("contour.__main__", fromlist=["main"]).main)
    assert "cycle=state.next_cycle()" in src, (
        "run_cycle is being called without a cycle number again")


# --- the pre-open cron -----------------------------------------------------
PREOPEN = datetime(2026, 9, 2, 9, 20, tzinfo=ET)


def test_the_pre_open_cycle_plans_the_day(isolated_state, monkeypatch):
    """The 13:20 UTC cron exists to parse the day's event blackouts before the
    open. It resolves to CLOSED and used to return having written a heartbeat
    and nothing else, while the docs described it as planning the day."""
    patch_chains(monkeypatch, {})
    window = Blackout(datetime(2026, 9, 2, 8, 10, tzinfo=ET),
                      datetime(2026, 9, 2, 8, 50, tzinfo=ET), "ADP 08:15 ET")
    mind = StubMind(windows=(window,))

    res, _, recs = cycle(isolated_state, now_et=PREOPEN, market_open=False,
                         mind=mind)

    assert res.mode == "CLOSED"
    assert mind.blackout_days == [PREOPEN.date()], "the brain was never asked"
    plan = next(r for r in recs if r["event"] == "plan")
    assert plan["windows"][0]["reason"] == "ADP 08:15 ET"
    assert plan["brain"] == "stub:model"
    published = json.loads((isolated_state / "plan.json").read_text())
    assert published["windows"] == plan["windows"]


def test_a_closed_day_does_not_wake_the_brain(isolated_state, monkeypatch):
    """Saturday is CLOSED too. Only a contest weekday before the bell plans."""
    patch_chains(monkeypatch, {})
    mind = StubMind()
    _, _, recs = cycle(isolated_state,
                       now_et=datetime(2026, 9, 5, 9, 20, tzinfo=ET),
                       market_open=False, mind=mind)
    assert mind.blackout_days == []
    assert not [r for r in recs if r["event"] == "plan"]


def test_a_mid_session_manage_only_cycle_does_not_plan(isolated_state,
                                                       monkeypatch):
    patch_chains(monkeypatch, {})
    mind = StubMind()
    _, _, recs = cycle(isolated_state,
                       now_et=datetime(2026, 9, 2, 15, 30, tzinfo=ET),
                       mind=mind)
    assert mind.blackout_days == []
    assert not [r for r in recs if r["event"] == "plan"]


# --- an unbalanced fill stops the cycle -----------------------------------
def fake_fill(balanced: bool):
    def submit(broker, cand, base, journal, **kw):
        return {"order_id": "abc", "status": "filled",
                "requested_qty": cand.contracts, "filled_qty": 1,
                "partial": False, "legs_balanced": balanced,
                "legs": [{"symbol": "P749", "side": "sell", "filled_qty": 1,
                          "filled_avg_price": 1.25},
                         {"symbol": "P744", "side": "buy", "filled_qty": 1,
                          "filled_avg_price": 0.92}]}
    return submit


def test_an_unbalanced_fill_stops_the_cycle_opening_anything_else(
        isolated_state, monkeypatch):
    """What is at the broker is no longer the defined-risk structure G3 sized,
    so the book's risk is not a number we can compute -- and opening more
    against an unknown is the one move that makes it worse."""
    patch_chains(monkeypatch, {"SPY": (measurement("SPY"), chain())})
    monkeypatch.setattr(L, "submit_with_ladder", fake_fill(balanced=False))

    res, _, recs = cycle(isolated_state, dry=False, mind=None)

    assert [d["underlying"] for d in res.decisions] == ["SPY"], (
        "the cycle carried on to the next name with an unrepaired book")
    halt = next(r for r in recs if r["event"] == "entries_halted")
    assert "repair_book" in halt["reason"]
    assert len(P.load()) == 1, "the position still has to be written down"


def test_a_balanced_fill_lets_the_cycle_finish(isolated_state, monkeypatch):
    """The control. Without it the test above passes on any early return."""
    patch_chains(monkeypatch, {"SPY": (measurement("SPY"), chain())})
    monkeypatch.setattr(L, "submit_with_ladder", fake_fill(balanced=True))

    res, _, recs = cycle(isolated_state, dry=False, mind=None)

    assert [d["underlying"] for d in res.decisions] == list(C.UNIVERSE)
    assert not [r for r in recs if r["event"] == "entries_halted"]
    assert next(r for r in recs
                if r["event"] == "position_opened")["legs_balanced"] is True


# --- sizing comes from measured trend, not from a model that anchored ------
def test_the_cycle_sizes_from_the_measured_regime_not_the_model(isolated_state,
                                                               monkeypatch):
    """The bug this module exists for: the model returned 0.5 sixteen times.

    A confirmed uptrend must size at the LRS weight, not at whatever the
    advisory layer says -- and the record has to show which one was used.
    """
    patch_chains(monkeypatch, {"SPY": (measurement(), chain())})
    sizes: list[float] = []
    real_build = L.S.build
    monkeypatch.setattr(L.S, "build",
                        lambda u, st, sd, nav: sizes.append(nav) or
                        real_build(u, st, sd, nav))
    class Anchored(StubMind):
        """What the live brain actually did: 0.5, every call, regardless."""
        def regime(self, day, vrp):
            return Advice((), 0.5, None, "llm", "vol premium is thin")

    _, _, recs = cycle(tmp_path=isolated_state, mind=Anchored(), ds=Src("up"))

    reg = [r for r in recs if r["event"] == "regime"]
    assert reg and reg[0]["source"] == "measured"
    assert reg[0]["weight"] == 1.0
    # 100_000, not the 50_000 the anchored multiplier would have produced.
    assert sizes == [100_000.0], "the anchored multiplier still sized the book"


def test_the_regime_is_read_once_per_underlying_per_cycle(isolated_state,
                                                          monkeypatch):
    patch_chains(monkeypatch, {u: (measurement(u), chain()) for u in C.UNIVERSE})
    src = Src("up")
    cycle(tmp_path=isolated_state, mind=StubMind(), ds=src)
    asked = [u for u, n in src.closes_asked if n == C.REGIME_LOOKBACK]
    assert sorted(asked) == sorted(set(asked)), "regime read more than once"


def test_a_source_without_regime_history_degrades_instead_of_failing(
        isolated_state, monkeypatch):
    """An old fixture must still replay. Half size, said out loud."""
    patch_chains(monkeypatch, {"SPY": (measurement(), chain())})
    _, _, recs = cycle(tmp_path=isolated_state, mind=StubMind(), ds=Src(None))

    reg = [r for r in recs if r["event"] == "regime"]
    assert reg and reg[0]["source"] == "degraded"
    assert reg[0]["weight"] == C.REGIME_DEGRADED_W
    assert "closes unavailable" in reg[0]["notes"]


def test_a_downtrend_stands_the_name_down_without_any_model(isolated_state,
                                                            monkeypatch):
    patch_chains(monkeypatch, {"SPY": (measurement(), chain())})
    _, _, recs = cycle(tmp_path=isolated_state, mind=StubMind(), ds=Src("down"))

    reg = [r for r in recs if r["event"] == "regime"][0]
    assert (reg["stage2"], reg["ribbon_bull"], reg["weight"]) == (False, False, 0.0)
    assert not [r for r in recs if r["event"] == "position_opened"]


def test_the_model_keeps_the_kill_switch_it_did_not_lose_it(isolated_state,
                                                            monkeypatch):
    """Sizing moved, standing down did not. A zero from the brain still halts."""
    patch_chains(monkeypatch, {"SPY": (measurement(), chain())})

    class Dead(StubMind):
        def regime(self, day, vrp):
            return Advice((), 0.0, None, "llm", "fail closed")

    _, _, recs = cycle(tmp_path=isolated_state, mind=Dead(), ds=Src("up"))
    ends = [r for r in recs if r["event"] == "cycle_end"]
    assert ends and ends[0].get("stand_down") is True


def test_the_journal_says_the_multiplier_no_longer_sizes(isolated_state,
                                                         monkeypatch):
    patch_chains(monkeypatch, {"SPY": (measurement(), chain())})
    _, _, recs = cycle(tmp_path=isolated_state, mind=StubMind(), ds=Src("up"))
    mind_rec = [r for r in recs if r["event"] == "mind"][0]
    assert "stand-down only" in mind_rec["multiplier_role"]


# --- what the freeze audit found, pinned so it cannot come back -----------
def test_a_non_unit_regime_weight_actually_reaches_the_sizing_nav(
        isolated_state, monkeypatch):
    """The audit mutated `nav * reg.weight` -> `nav` and got 165 green.

    Today's live weight is 1.0, so multiplying by it is a no-op and every
    assertion written against live values passes with the sizer deleted. This
    forces a weight that is neither 0 nor 1, so the multiplication is load
    bearing and a mutation cannot hide.
    """
    from contour.regime import Regime
    patch_chains(monkeypatch, {"SPY": (measurement(), chain())})
    monkeypatch.setattr(L.regime, "assess", lambda u, c: Regime(
        u, True, True, 0.25, 0.25, "measured", "forced"))
    sizes: list[float] = []
    real = L.S.build
    monkeypatch.setattr(L.S, "build",
                        lambda u, st, sd, nav: sizes.append(nav) or real(u, st, sd, nav))
    cycle(tmp_path=isolated_state, mind=StubMind(), ds=Src("up"))
    assert sizes == [25_000.0], "the regime weight never reached S.build"


def test_an_absent_brain_still_halves_the_book(isolated_state, monkeypatch):
    """The tier three judged documents promise: no brain -> half size.

    Sizing moved out of the model, and this tier moved out with it by
    accident -- a missing LLM key doubled live size instead of halving it.
    It is a policy response to missing information, not the model's judgement,
    so it must survive the model losing its sizing job.
    """
    patch_chains(monkeypatch, {"SPY": (measurement(), chain())})

    class NoProvider(StubMind):
        brain = "degraded"
        def blackouts(self, day, headlines=()):
            return Advice((), 0.5, None, "degraded", "no LLM provider configured")
        def regime(self, day, vrp):
            return Advice((), 0.5, None, "degraded", "no LLM provider configured")

    sizes: list[float] = []
    real = L.S.build
    monkeypatch.setattr(L.S, "build",
                        lambda u, st, sd, nav: sizes.append(nav) or real(u, st, sd, nav))
    _, _, recs = cycle(tmp_path=isolated_state, mind=NoProvider(), ds=Src("up"))

    assert sizes == [50_000.0], "the degraded-brain half-size tier is inert"
    assert [r for r in recs if r["event"] == "mind"][0]["brain_floor"] == 0.5


def test_an_answering_model_can_no_longer_shrink_the_book_by_anchoring(
        isolated_state, monkeypatch):
    """The floor must not become a back door for the 0.5 that started this.

    A CONFIGURED brain returning 0.5 is the anchoring bug. Only `source ==
    "degraded"` -- no provider at all -- may halve.
    """
    patch_chains(monkeypatch, {"SPY": (measurement(), chain())})

    class Anchored(StubMind):
        def regime(self, day, vrp):
            return Advice((), 0.5, None, "llm", "vol premium is thin")

    sizes: list[float] = []
    real = L.S.build
    monkeypatch.setattr(L.S, "build",
                        lambda u, st, sd, nav: sizes.append(nav) or real(u, st, sd, nav))
    cycle(tmp_path=isolated_state, mind=Anchored(), ds=Src("up"))
    assert sizes == [100_000.0], "an answering model got its sizing job back"


def test_a_measured_stand_down_is_not_blamed_on_the_option_chain(
        isolated_state, monkeypatch):
    """Weight 0 used to fall through to "could not assemble a valid structure
    from the chain" -- the same misattribution loop.py already fixes for the
    LLM stand-down 45 lines earlier, reintroduced for the sizer."""
    patch_chains(monkeypatch, {"SPY": (measurement(), chain())})
    _, _, recs = cycle(tmp_path=isolated_state, mind=StubMind(), ds=Src("down"))

    d = [r for r in recs if r["event"] == "decision"][0]
    assert d["reason"].startswith("STAND_DOWN:")
    assert "no trend support" in d["reason"]
    assert "could not assemble" not in d["reason"]


# --- the sizer is published, not only journaled ---------------------------
# The regime replaced the model as the thing that sizes the book, and for a
# day it existed only inside `journal/*.jsonl`. The dashboard is the artifact
# anyone actually opens; a sizing policy nothing renders is a sizing policy
# nobody can check.
def test_the_sizer_is_published_and_not_only_journaled(isolated_state):
    with pytest.MonkeyPatch().context() as mp:
        patch_chains(mp, {"SPY": (measurement("SPY"), chain()),
                          "QQQ": (measurement("QQQ", vrp=1.05), chain())})
        cycle(isolated_state)

    rows = json.loads((isolated_state / "regime.json").read_text())
    assert [r["underlying"] for r in rows] == ["SPY", "QQQ"], (
        "published in universe order, and only what was measured")
    for r in rows:
        assert set(r) >= {"weight", "source", "stage2", "ribbon_bull",
                          "lrs_weight", "notes"}
        assert 0.0 <= r["weight"] <= 1.0


def test_the_publish_time_is_stamped_so_the_page_can_age_it(isolated_state):
    """Without the stamp the panel borrows the heartbeat, which ticks on
    CLOSED cycles too, and reports Thursday's sizing as 2 minutes old."""
    with pytest.MonkeyPatch().context() as mp:
        patch_chains(mp, {"SPY": (measurement("SPY"), chain())})
        cycle(isolated_state)
    assert "regime" in json.loads((isolated_state / "written_at.json").read_text())


def test_every_decision_carries_the_weight_that_sized_it(isolated_state):
    """Including the refusals. A name refused on a half-size book was refused
    on different terms than one refused at full size, and the contracts field
    cannot say so for a trade that never happened."""
    with pytest.MonkeyPatch().context() as mp:
        patch_chains(mp, {"SPY": (measurement("SPY"), chain()),
                          "QQQ": (measurement("QQQ", vrp=1.05), chain())})
        res, _, _ = cycle(isolated_state)

    qqq = next(d for d in res.decisions if d["underlying"] == "QQQ")
    assert qqq["decision"] == "NO_TRADE" and "VRP" in qqq["reason"]
    for d in res.decisions:
        if d["underlying"] == "IWM":
            # Unmeasurable: no chain, so no regime was ever computed. Stamping
            # one here would be inventing a reading, not reporting one.
            assert "regime_weight" not in d
            continue
        assert d["regime_source"] == "measured", d
        assert d["regime_weight"] is not None


def test_a_stand_down_does_not_blank_the_published_sizing(isolated_state):
    """A cycle that halts before the entry loop measured no regime. Writing
    `[]` would erase the last real reading AND stamp it fresh, so the page
    would report "no sizing published" as though that were current news."""
    (isolated_state / "regime.json").write_text('[{"underlying": "SPY"}]\n')

    class Halt(StubMind):
        def regime(self, day, vrp):
            return Advice((), 0.0, None, "llm", "stand down")

    with pytest.MonkeyPatch().context() as mp:
        patch_chains(mp, {"SPY": (measurement("SPY"), chain())})
        cycle(isolated_state, mind=Halt())

    kept = json.loads((isolated_state / "regime.json").read_text())
    assert kept == [{"underlying": "SPY"}], "the last real reading was erased"


def test_a_non_trading_cycle_still_publishes_the_nav(isolated_state,
                                                     monkeypatch):
    """The dashboard's headline P&L comes from the equity series, and only
    TRADE cycles appended to it -- so from the flatten onwards the published
    number froze while the account kept moving. On the verify-only Friday that
    put the dashboard and the write-up in open disagreement, which is the one
    thing a submission built on "check it yourself" cannot afford."""
    patch_chains(monkeypatch, {})
    cycle(isolated_state, now_et=PREOPEN, market_open=False, mind=StubMind())

    points = json.loads((isolated_state / "equity.json").read_text())
    assert points, "a non-TRADE cycle published no NAV at all"
    assert points[-1]["mode"] == "CLOSED"
    assert points[-1]["nav"] > 0


def test_a_dev_cycle_publishes_nothing_to_the_judged_series(tmp_path,
                                                            monkeypatch):
    """--dev exists so the whole loop can be exercised without touching the
    judged account. But state/ is published to the branch the dashboard reads,
    so a dev cycle used to edit what the public sees: a manual dispatch
    defaults dev=true, and one published $99,999.94 from the throwaway account
    into a series whose real value was $99,503.70 -- a spike on the public
    chart, sourced from a different account entirely."""
    from contour import state

    monkeypatch.setattr(state, "ROOT", tmp_path / "state")
    monkeypatch.setattr(state, "_SUPPRESSED", None)
    state.write("heartbeat", {"cycle_count": 1})
    assert (tmp_path / "state" / "heartbeat.json").exists()

    monkeypatch.setattr(state, "_SUPPRESSED", "dev account")
    state.point("equity", {"nav": 99_999.94})
    state.write("heartbeat", {"cycle_count": 2})

    assert not (tmp_path / "state" / "equity.json").exists(), (
        "a dev cycle wrote into the published equity series")
    published = json.loads(
        (tmp_path / "state" / "heartbeat.json").read_text())
    assert published["cycle_count"] == 1, "a dev cycle overwrote the heartbeat"

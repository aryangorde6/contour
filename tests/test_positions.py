"""The open book must survive between cron runs.

Every cycle is a fresh container. A position that is not written down is a
position nobody manages: `run_cycle` gets an empty `open_positions`, so the
profit target, the stop, the breach rule and the scheduled Thursday flatten
all iterate an empty tuple, and `Book(positions=())` reports zero open risk
however much the account actually holds.

This happened for real on 2026-08-31: two SPY condors filled at 10:09 and
10:39 ET and no later cycle could see them.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest

from contour import config as C
from contour import positions as P
from contour import state
from contour.journal import Journal
from contour.loop import run_cycle
from contour.manage import ManagedPosition
from contour.mind import Mind

from .test_manage import condor, pos

ET = C.ET


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "ROOT", tmp_path)
    yield tmp_path


# --- serialisation -------------------------------------------------------
def test_a_position_survives_the_round_trip(isolated_state):
    original = pos(credit=0.87)
    P.save([original])
    back = P.load()
    assert len(back) == 1
    got = back[0]
    assert got.order_id == original.order_id
    assert got.credit_received == original.credit_received
    assert got.opened_at == original.opened_at
    assert got.candidate.underlying == "SPY"
    assert got.candidate.contracts == original.candidate.contracts
    assert [l.symbol for l in got.candidate.legs] == \
           [l.symbol for l in original.candidate.legs]
    assert isinstance(got.candidate.legs[0].expiration_date, date)
    assert got.candidate.legs[0].expiration_date == C.EXPIRY


def test_a_corrupt_book_never_raises(isolated_state):
    (isolated_state / "positions.json").write_text("{not json")
    assert P.load() == []


def test_one_unreadable_entry_does_not_lose_the_others(isolated_state):
    P.save([pos()])
    raw = json.loads((isolated_state / "positions.json").read_text())
    raw.append({"candidate": {"broken": True}})
    (isolated_state / "positions.json").write_text(json.dumps(raw))
    assert len(P.load()) == 1


# --- the credit the stop is measured against -----------------------------
def test_credit_comes_from_the_actual_fills_not_the_mid():
    """Verified against the live 2026-08-31 fill: 1.25 - 0.92 + 0.78 - 0.31."""
    rec = {"filled_qty": 1, "legs": [
        {"symbol": "P745", "side": "sell", "filled_qty": 1, "filled_avg_price": 1.25},
        {"symbol": "P740", "side": "buy", "filled_qty": 1, "filled_avg_price": 0.92},
        {"symbol": "C781", "side": "sell", "filled_qty": 1, "filled_avg_price": 0.78},
        {"symbol": "C786", "side": "buy", "filled_qty": 1, "filled_avg_price": 0.31}]}
    assert P.credit_from_fill(rec, fallback=0.825) == pytest.approx(0.80)


def test_credit_falls_back_only_when_no_leg_price_is_served():
    assert P.credit_from_fill({"legs": []}, fallback=0.825) == 0.825
    assert P.credit_from_fill(
        {"legs": [{"side": "sell", "filled_qty": 1, "filled_avg_price": None}]},
        fallback=0.825) == 0.825


# --- the regression that matters -----------------------------------------
class FakeBroker:
    def __init__(self):
        self.closed = []

    def account(self):
        return {"equity": 100_000.0, "account_number": "TEST"}


class Src:
    """Enough chain to measure but never enough to trade, so the entry path
    stays out of the way of the exit assertions."""

    def spot(self, u):
        return {"SPY": 769.30, "QQQ": 716.95, "IWM": 295.86}[u]

    def closes(self, u, n=11):
        return [700.0 + i for i in range(n)]

    def legs(self, u, expiry, spot):
        return []


def test_a_persisted_position_is_exit_checked_on_the_next_cycle(isolated_state,
                                                                tmp_path):
    """The whole point: cycle N writes it down, cycle N+1 manages it."""
    P.save([pos(credit=0.87)])

    j = Journal(tmp_path / "j.jsonl")
    res = run_cycle(ds=Src(), broker=FakeBroker(),
                    now_et=datetime(2026, 9, 1, 12, 0, tzinfo=ET),
                    market_open=True, journal=j, dry=True,
                    open_positions=P.load(), mind=Mind(api_key=""))

    assert len(res.exits) == 1, "the restored position was not exit-checked"
    assert res.exits[0]["order_id"] == "o1"
    assert any(r.payload.get("event") == "exit_check" for r in j.read())


def test_the_flatten_day_actually_reaches_a_restored_position(isolated_state,
                                                              tmp_path):
    """TECHNICAL.md claims the account is flat before the deadline. That is
    only true if Thursday's cycle can see the book."""
    P.save([pos(credit=0.87)])
    j = Journal(tmp_path / "j.jsonl")
    res = run_cycle(ds=Src(), broker=FakeBroker(),
                    now_et=datetime(2026, 9, 3, 15, 50, tzinfo=ET),
                    market_open=True, journal=j, dry=True,
                    open_positions=P.load(), mind=Mind(api_key=""))
    assert res.exits and res.exits[0]["exit"] is True
    assert "FLATTEN" in res.exits[0]["reason"]


def test_an_empty_book_still_means_no_exits(isolated_state, tmp_path):
    j = Journal(tmp_path / "j.jsonl")
    res = run_cycle(ds=Src(), broker=FakeBroker(),
                    now_et=datetime(2026, 9, 1, 12, 0, tzinfo=ET),
                    market_open=True, journal=j, dry=True,
                    open_positions=P.load(), mind=Mind(api_key=""))
    assert res.exits == []


# --- the entry half: a fill that is not written down is unmanaged ---------
def test_a_fill_is_persisted_with_the_quantity_that_actually_filled(
        isolated_state, tmp_path, monkeypatch):
    """A partial fill leaves fewer contracts than the candidate describes; an
    exit sized off the request would try to close what we never held.

    Only the broker call is faked. Measurement, structure selection, strike
    assembly, sizing and all twelve gates run for real.
    """
    import contour.loop as L
    from contour.models import Measurement

    from .test_gates import leg

    chain = [
        leg(side="buy", strike=749.0, delta=-0.13, otype="put", bid=1.27, ask=1.32),
        leg(side="buy", strike=744.0, delta=-0.06, otype="put", bid=0.95, ask=0.96),
        leg(side="buy", strike=785.0, delta=0.13, otype="call", bid=0.85, ask=0.90),
        leg(side="buy", strike=790.0, delta=0.06, otype="call", bid=0.34, ask=0.35),
    ]
    for l in chain:
        object.__setattr__(l, "quote_age_s", 30.0)

    m = Measurement(underlying="SPY", spot=769.0, atm_iv=11.4, rv10=7.8,
                    vrp_ratio=1.45, skew25=2.52, skew_z=0.0)

    monkeypatch.setattr(
        L, "measure_underlying",
        lambda ds, und, exp: (m, chain) if und == "SPY" else None)

    def fake_submit(broker, cand, base, journal, **kw):
        assert cand.contracts >= 1
        return {"order_id": "abc123", "status": "filled",
                "requested_qty": cand.contracts, "filled_qty": 1,
                "partial": False, "legs_balanced": True,
                "legs": [
                    {"symbol": "P749", "side": "sell", "filled_qty": 1,
                     "filled_avg_price": 1.25},
                    {"symbol": "P744", "side": "buy", "filled_qty": 1,
                     "filled_avg_price": 0.92}]}

    monkeypatch.setattr(L, "submit_with_ladder", fake_submit)

    j = Journal(tmp_path / "j.jsonl")
    run_cycle(ds=Src(), broker=FakeBroker(),
              now_et=datetime(2026, 9, 1, 12, 0, tzinfo=ET),
              market_open=True, journal=j, dry=False,
              open_positions=[], mind=None)

    book = P.load()
    assert len(book) == 1, "the fill was not written to the book"
    assert book[0].order_id == "abc123"
    assert book[0].candidate.contracts == 1, "recorded the request, not the fill"
    assert book[0].credit_received == pytest.approx(0.33)
    assert any(r.payload.get("event") == "position_opened" for r in j.read())


# --- exits must be priced off TODAY's chain, not entry-time quotes --------
def test_mark_is_repriced_from_the_live_chain(isolated_state, tmp_path):
    """The stored legs keep their entry quotes forever. Pricing off them
    freezes mark at the entry credit, so the profit target and the stop can
    never fire -- the position rides to expiry whatever it does."""
    from dataclasses import replace as _replace

    p = pos(credit=0.87)
    entry_mark = abs(sum((l.bid + l.ask) / 2 * (1 if l.is_short else -1)
                         for l in p.candidate.legs))
    P.save([p])

    # the same strikes, now worth a quarter of what they were: a profit target
    halved = [_replace(l, bid=l.bid * 0.25, ask=l.ask * 0.25)
              for l in p.candidate.legs]
    # measure_underlying needs 25-delta strikes on both wings to produce a
    # Measurement at all; without them the chain is unmeasurable and the test
    # would pass vacuously through the unpriced branch.
    from .test_gates import leg as _leg
    surround = [
        _leg(side="buy", strike=760.0, delta=-0.25, otype="put", bid=3.0, ask=3.1),
        _leg(side="buy", strike=778.0, delta=0.25, otype="call", bid=3.0, ask=3.1),
    ]

    class Cheap(Src):
        def legs(self, u, expiry, spot):
            return (halved + surround) if u == "SPY" else []

    j = Journal(tmp_path / "j.jsonl")
    res = run_cycle(ds=Cheap(), broker=FakeBroker(),
                    now_et=datetime(2026, 9, 1, 12, 0, tzinfo=ET),
                    market_open=True, journal=j, dry=True,
                    open_positions=P.load(), mind=Mind(api_key=""))

    assert res.exits[0]["mark"] < entry_mark * 0.5, "mark was not re-priced"
    assert res.exits[0]["exit"] is True
    assert "PROFIT_TARGET" in res.exits[0]["reason"]


def test_an_unpriceable_position_does_not_fire_a_phantom_breach(isolated_state,
                                                                tmp_path):
    """spot 0.0 reads as far below every short put. Falling back to it turns a
    data outage into a forced exit at whatever the book can be closed for."""
    P.save([pos(credit=0.87)])
    j = Journal(tmp_path / "j.jsonl")
    res = run_cycle(ds=Src(), broker=FakeBroker(),          # Src serves no legs
                    now_et=datetime(2026, 9, 1, 12, 0, tzinfo=ET),
                    market_open=True, journal=j, dry=True,
                    open_positions=P.load(), mind=Mind(api_key=""))
    assert res.exits[0]["exit"] is False
    assert "HOLD_UNPRICED" in res.exits[0]["reason"]


def test_the_clock_rule_still_fires_when_nothing_can_be_priced(isolated_state,
                                                               tmp_path):
    """A data outage must not postpone the Thursday flatten."""
    P.save([pos(credit=0.87)])
    j = Journal(tmp_path / "j.jsonl")
    res = run_cycle(ds=Src(), broker=FakeBroker(),
                    now_et=datetime(2026, 9, 3, 15, 50, tzinfo=ET),
                    market_open=True, journal=j, dry=True,
                    open_positions=P.load(), mind=Mind(api_key=""))
    assert res.exits[0]["exit"] is True
    assert "FLATTEN" in res.exits[0]["reason"]


# --- a stand-down must read as a stand-down ------------------------------
def test_a_failed_closed_brain_is_journaled_as_a_stand_down(isolated_state,
                                                            tmp_path):
    """multiplier 0 sizes every candidate to zero contracts, so S.build returns
    None and the old code journaled "could not assemble a valid structure from
    the chain" -- a brain outage reading as a market-data problem."""
    from contour.mind import Mind as RealMind

    m = RealMind(api_key="fake-key")

    def boom(*_a, **_k):
        raise RuntimeError("connection reset")
    m._call = boom                                  # type: ignore[method-assign]

    j = Journal(tmp_path / "j.jsonl")
    res = run_cycle(ds=Src(), broker=FakeBroker(),
                    now_et=datetime(2026, 9, 1, 12, 0, tzinfo=ET),
                    market_open=True, journal=j, dry=True,
                    open_positions=[], mind=m)

    assert res.decisions, "a stand-down must still journal a decision per name"
    for d in res.decisions:
        assert d["decision"] == "NO_TRADE"
        assert "STAND_DOWN" in d["reason"]
        assert "chain" not in d["reason"].lower()
    assert any(r.payload.get("stand_down") for r in j.read())


# --- exit order ids must not poison the next attempt ---------------------
def test_close_ids_differ_across_cycles_but_not_within_one():
    """Alpaca 422s on a reused client_order_id and close_position only
    special-cases the uncovered-leg rejection, so a constant base id means the
    first failed close permanently blocks every later one -- including the
    Thursday flatten."""
    from contour.loop import close_base_id

    p = pos()
    a = close_base_id(p, datetime(2026, 9, 3, 15, 47, tzinfo=ET))
    b = close_base_id(p, datetime(2026, 9, 3, 15, 49, tzinfo=ET))   # same bucket
    c = close_base_id(p, datetime(2026, 9, 3, 16, 2, tzinfo=ET))    # next bucket

    assert a == b, "a retried cron inside one cycle must stay idempotent"
    assert a != c, "the next cycle must be able to try again"
    assert p.order_id in a
    assert len(f"{c}-c5") <= 128, "client_order_id must fit Alpaca's limit"

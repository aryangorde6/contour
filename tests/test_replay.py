"""Record once, replay forever -- and get the same answer every time.

The write-up claims a judge with no Alpaca credentials can rerun the exact
pipeline that produced our published numbers. These tests are what makes that
claim checkable rather than decorative.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest

from contour import config as C
from contour.models import Leg
from contour.replay import (FORMAT, Recorder, Replay, ReplayBroker,
                            ReplayError, _leg_from_dict, _leg_to_dict)

from .test_gates import leg

AS_OF = datetime(2026, 8, 31, 11, 0, tzinfo=C.ET)


class FakeSource:
    """A DataSource that counts calls, so we can prove replay stops making
    them -- 'needs no credentials' means nothing if it still reaches out."""

    def __init__(self):
        self.calls = 0

    def spot(self, underlying):
        self.calls += 1
        return {"SPY": 769.30, "QQQ": 716.95, "IWM": 295.86}[underlying]

    def closes(self, underlying, n=11):
        self.calls += 1
        return [700.0 + i for i in range(n)]

    def legs(self, underlying, expiry, spot):
        self.calls += 1
        return [leg(strike=749.0, delta=-0.25, bid=1.27, ask=1.32),
                leg(strike=744.0, delta=-0.06, bid=0.95, ask=0.96),
                leg(strike=785.0, delta=0.25, otype="call", bid=0.85, ask=0.90)]


def recorded(tmp_path):
    src = FakeSource()
    rec = Recorder(src, tmp_path / "fx.json")
    rec.spot("SPY")
    rec.closes("SPY", 11)
    rec.legs("SPY", C.EXPIRY, 769.30)
    return src, rec, rec.save(AS_OF)


# --- the round trip ------------------------------------------------------
def test_replay_returns_exactly_what_was_recorded(tmp_path):
    src, _, path = recorded(tmp_path)
    live_calls = src.calls

    fx = Replay.load(path)
    assert fx.spot("SPY") == 769.30
    assert fx.closes("SPY", 11) == [700.0 + i for i in range(11)]
    got = fx.legs("SPY", C.EXPIRY, 769.30)
    assert [l.symbol for l in got] == ["SPY260911P00749000",
                                       "SPY260911P00744000",
                                       "SPY260911P00785000"]
    assert [l.delta for l in got] == [-0.25, -0.06, 0.25]
    assert src.calls == live_calls, "replay must not touch the live source"


def test_leg_survives_a_json_round_trip_including_the_nulls():
    """A greek that comes back as 0.0 instead of None would silently disarm
    G6, which exists to veto exactly that."""
    l = Leg(symbol="SPY260911P00749000", side="buy", ratio_qty=1,
            option_type="put", strike=749.0, expiration_date=C.EXPIRY,
            bid=1.27, ask=1.32, delta=None, implied_volatility=None,
            open_interest=5000, tradable=True, close_price=None,
            quote_age_s=None)
    back = _leg_from_dict(json.loads(json.dumps(_leg_to_dict(l))))
    assert back.delta is None and back.implied_volatility is None
    assert back.close_price is None and back.quote_age_s is None
    assert back.expiration_date == C.EXPIRY and isinstance(back.expiration_date, date)


def test_the_capture_time_is_part_of_the_fixture(tmp_path):
    """Replay restores it as 'now'. Without it every recorded quote reads as
    hours stale and G5 vetoes the whole chain."""
    _, _, path = recorded(tmp_path)
    assert Replay.load(path).as_of_et == AS_OF


# --- failure modes name the problem --------------------------------------
def test_a_missing_key_says_what_was_recorded(tmp_path):
    _, _, path = recorded(tmp_path)
    fx = Replay.load(path)
    with pytest.raises(ReplayError, match="no spot for 'IWM'"):
        fx.spot("IWM")
    with pytest.raises(ReplayError, match=r"no legs for 'IWM\|2026-09-11'"):
        fx.legs("IWM", C.EXPIRY, 200.0)


def test_a_fixture_from_another_format_is_refused(tmp_path):
    with pytest.raises(ReplayError, match="format 99"):
        Replay({"format": 99})


def test_no_fixtures_tells_you_how_to_make_one(tmp_path):
    with pytest.raises(ReplayError, match="--record"):
        Replay.newest(tmp_path)


def test_newest_wins(tmp_path):
    """The last recording wins, whatever the operator called the file."""
    for name in ("2026-08-29.json", "2026-08-31.json", "aaa-recorded-last.json"):
        Recorder(FakeSource(), tmp_path / name).save(AS_OF)
    assert Replay.newest(tmp_path).path.name == "aaa-recorded-last.json"


# --- the broker cannot trade, structurally -------------------------------
def test_the_replay_broker_has_no_way_to_place_an_order():
    """Replay forces dry, but a broker that *could* submit is one bad flag
    away from a real order. It simply does not have the method."""
    b = ReplayBroker()
    assert b.account()["equity"] == 100_000.0
    for forbidden in ("submit", "submit_order", "place_order", "close_position",
                      "cancel", "assert_account"):
        assert not hasattr(b, forbidden), forbidden


# --- determinism, which is the whole point -------------------------------
def test_two_replays_of_one_fixture_produce_identical_journals(tmp_path):
    from contour import state
    from contour.journal import Journal
    from contour.loop import run_cycle
    from contour.mind import Mind

    src = FakeSource()
    rec = Recorder(src, tmp_path / "fx.json")
    for und in C.UNIVERSE:
        rec.spot(und); rec.closes(und, 11); rec.legs(und, C.EXPIRY, 769.30)
    fx = Replay.load(rec.save(AS_OF))

    def once(tag):
        state.ROOT = tmp_path / tag
        j = Journal(tmp_path / f"{tag}.jsonl")
        run_cycle(ds=fx, broker=ReplayBroker(), now_et=fx.as_of_et,
                  market_open=True, journal=j, dry=True, mind=Mind(api_key=""))
        ok, msg = Journal(tmp_path / f"{tag}.jsonl").verify()
        assert ok, msg
        return [r.payload for r in Journal(tmp_path / f"{tag}.jsonl").read()]

    a, b = once("a"), once("b")
    assert a == b, "the same fixture must yield the same decisions"
    assert any(p.get("event") == "decision" for p in a), "nothing was decided"


# --- what the rehearsal actually shows a judge ---------------------------
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "2026-08-31-preopen.json"


def test_replay_prints_every_gate_reason_not_only_the_refusals(tmp_path,
                                                               monkeypatch,
                                                               capsys):
    """README and WRITEUP both promise every gate reason. Printing only the
    failures shows that the agent stopped, not that it checked -- and "it
    refused" is a much weaker claim than "here is the whole evaluation"."""
    from contour import state
    from contour.__main__ import _run_replay

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(state, "ROOT", tmp_path / "state")
    assert _run_replay(Replay.load(FIXTURE)) == 0

    out = capsys.readouterr().out
    printed = [l for l in out.splitlines() if "[ok  ]" in l or "[VETO]" in l]
    assert printed, "no gate reasons were printed at all"
    assert any("[ok  ] G1 ok" in l for l in printed), (
        "passing gates are still invisible")
    assert any("[VETO]" in l for l in printed), "the refusal is not marked"

    # Every reason the engine returned, and nothing invented -- across BOTH
    # books. The sleeve is a second set of gates on the same account, and a
    # rehearsal that printed twelve green G-gates while saying nothing about
    # S1-S7 would be showing a judge half the risk surface.
    reasons = [g for line in Path("replay_out/journal").glob("*.jsonl")
               for rec in [json.loads(l) for l in line.read_text().splitlines()]
               if rec["payload"].get("event") in ("decision", "sleeve_decision")
               for g in rec["payload"].get("gates", [])]
    assert len(printed) == len(reasons)
    assert any("[ok  ] S1 ok" in l for l in printed), (
        "the sleeve's gates are not shown at all")


# --- picking the fixture to demo -------------------------------------------
def test_the_newest_fixture_is_the_newest_recording_not_the_last_filename(
        tmp_path):
    """`1305et` sorts before `preopen` but was recorded four hours later.

    Filename order served the pre-open fixture, whose quotes are hours stale,
    so the `--replay` demo showed a G5 staleness veto instead of the twelve
    green gates the README and the write-up both promise.
    """
    import json as _json
    from contour.replay import Replay

    base = {"format": FORMAT, "as_of_et": "2026-08-31T11:00:00-04:00",
            "spot": {}, "closes": {}, "legs": {}}
    (tmp_path / "2026-08-31-preopen.json").write_text(_json.dumps(
        {**base, "captured_utc": "2026-08-31T13:19:59+00:00"}))
    (tmp_path / "2026-08-31-1305et.json").write_text(_json.dumps(
        {**base, "captured_utc": "2026-08-31T17:05:00+00:00"}))

    assert Replay.newest(tmp_path).path.name == "2026-08-31-1305et.json"


def test_an_unreadable_fixture_never_wins_the_selection(tmp_path):
    import json as _json
    from contour.replay import Replay

    (tmp_path / "good.json").write_text(_json.dumps(
        {"format": FORMAT, "as_of_et": "2026-08-31T11:00:00-04:00",
         "captured_utc": "2026-08-31T17:05:00+00:00",
         "spot": {}, "closes": {}, "legs": {}}))
    (tmp_path / "zzz-broken.json").write_text("{not json")

    assert Replay.newest(tmp_path).path.name == "good.json"


def test_replay_shows_the_regime_it_sized_on(tmp_path, monkeypatch, capsys):
    """The sizer owns 100% of position size. A rehearsal that never prints it
    can show twelve green gates while every name sits at half weight because
    the fixture predates the lookback -- green gates, green CI, exit 0."""
    from contour import state
    from contour.__main__ import _run_replay

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(state, "ROOT", tmp_path / "state")
    assert _run_replay(Replay.newest(
        Path(__file__).resolve().parents[1] / "fixtures")) == 0

    out = capsys.readouterr().out
    lines = [l for l in out.splitlines() if "regime weight" in l]
    assert lines, "the replay never showed the weight it sized on"
    assert any("[measured]" in l for l in lines)


def test_a_degraded_regime_says_so_on_screen(tmp_path, monkeypatch, capsys):
    """An old fixture must announce that it degraded, not just look fine."""
    from contour import state
    from contour.__main__ import _run_replay

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(state, "ROOT", tmp_path / "state")
    _run_replay(Replay.load(FIXTURE))          # pre-regime fixture
    out = capsys.readouterr().out
    assert "[degraded]" in out, "silent degradation is invisible to a judge"


def test_the_replay_names_the_absent_brain_floor_that_also_sized_it(
        tmp_path, monkeypatch, capsys):
    """`--replay` runs with no provider, so the absent-brain tier halves the
    book on top of the trend weight. Printing "regime weight 1.0" beside a
    one-contract condor -- against a per-position cap that admits three --
    leaves a reader no way to reconcile the two numbers they can see."""
    from contour import config as C
    from contour import state
    from contour.__main__ import _run_replay

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(state, "ROOT", tmp_path / "state")
    assert _run_replay(Replay.newest(
        Path(__file__).resolve().parents[1] / "fixtures")) == 0

    out = capsys.readouterr().out
    assert "absent-brain floor" in out, (
        "the replay sized on a floor it never showed")
    assert str(C.DEGRADED_BRAIN_SIZE) in out

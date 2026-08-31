"""Record once, replay forever -- and get the same answer every time.

The write-up claims a judge with no Alpaca credentials can rerun the exact
pipeline that produced our published numbers. These tests are what makes that
claim checkable rather than decorative.
"""
from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from contour import config as C
from contour.models import Leg
from contour.replay import (Recorder, Replay, ReplayBroker, ReplayError,
                            _leg_from_dict, _leg_to_dict)

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
    for name in ("2026-08-29.json", "2026-08-31.json", "2026-08-30.json"):
        src = FakeSource()
        Recorder(src, tmp_path / name).save(AS_OF)
    assert Replay.newest(tmp_path).path.name == "2026-08-31.json"


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

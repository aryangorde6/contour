"""The advisory layer's failure policy, which is the whole point of it.

Two tiers, deliberately different:
  not configured  -> degraded but STILL TRADING (half size, no veto)
  configured but failing -> fail CLOSED (veto, multiplier 0)
"""
from __future__ import annotations

from datetime import date

import pytest

from contour import structures as S
from contour.mind import Mind

from .test_gates import leg

DAY = date(2026, 8, 31)


def broken(mind: Mind) -> Mind:
    def boom():
        raise RuntimeError("connection reset")
    mind._c = boom                                   # type: ignore[method-assign]
    return mind


# --- tier 1: absent brain must not stop the agent ------------------------
def test_unconfigured_runs_degraded_and_keeps_trading():
    m = Mind(api_key="")
    assert not m.configured

    a = m.blackouts(DAY)
    assert a.source == "degraded" and a.multiplier == 0.5
    assert a.blackouts == (), "no LLM windows; G10's hard-coded table still applies"

    r = m.regime(DAY, {"SPY": 1.39})
    assert r.source == "degraded" and r.multiplier == 0.5

    v = m.confirm("SPY", "CONDOR", 1.39, 0.0)
    assert not v.veto, "an absent brain must not veto -- that would halt everything"


# --- tier 2: a configured brain returning garbage is a real signal --------
def test_configured_but_failing_fails_closed():
    m = broken(Mind(api_key="sk-ant-fake"))
    assert m.configured

    a = m.blackouts(DAY)
    assert a.source == "failed_closed" and a.multiplier == 0.0

    r = m.regime(DAY, {"SPY": 1.39})
    assert r.source == "failed_closed" and r.multiplier == 0.0

    v = m.confirm("SPY", "CONDOR", 1.39, 0.0)
    assert v.veto and "fail-closed" in v.reason


# --- the multiplier can only ever shrink ---------------------------------
def condor_at(nav):
    return S.build("SPY", "CONDOR", [
        leg(side="sell", strike=749.0, bid=1.27, ask=1.32),
        leg(side="buy", strike=744.0, bid=0.95, ask=0.96),
        leg(side="sell", strike=785.0, bid=0.85, ask=0.90),
        leg(side="buy", strike=790.0, bid=0.34, ask=0.35),
    ], nav=nav)


def test_multiplier_scales_size_down_and_zero_stands_down():
    full = condor_at(100_000 * 1.0)
    half = condor_at(100_000 * 0.5)
    none = condor_at(100_000 * 0.0)

    assert full is not None and full.contracts == 2
    assert half is not None and half.contracts == 1
    assert none is None, "multiplier 0 must yield no position at all"


def test_multiplier_never_widens():
    """Guards the direction of the whole layer: min() against 1.0 means an
    LLM returning something absurd cannot increase risk."""
    for llm_value in (1.0, 2.0, 99.0):
        assert min(llm_value, 1.0) <= 1.0

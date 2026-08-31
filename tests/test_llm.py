"""The provider seam.

Two things matter here and nothing else does: that we can pull a valid object
out of whatever an open-weight model actually emits, and that the choice of
brain is predictable from the environment. Everything above this layer trusts
a raise to mean "the brain failed", so a silent wrong answer is the only real
danger.
"""
from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from contour import llm
from contour.llm import (AnthropicProvider, BedrockProvider, LLMError,
                         OpenAICompatProvider,
                         _first_json_object, build_provider)


class Toy(BaseModel):
    veto: bool
    reason: str


# --- pulling JSON out of a chatty model ----------------------------------
@pytest.mark.parametrize("raw", [
    '{"veto": true, "reason": "x"}',
    '```json\n{"veto": true, "reason": "x"}\n```',
    'Let me think about this.\n\n{"veto": true, "reason": "x"}',
    '{"veto": true, "reason": "x"}\n\nHope that helps!',
])
def test_extracts_object_from_real_world_replies(raw):
    assert json.loads(_first_json_object(raw))["veto"] is True


def test_brace_matching_survives_nesting_and_braces_in_strings():
    raw = 'thinking...\n{"veto": false, "reason": "the } is literal", "a": {"b": 1}}'
    got = json.loads(_first_json_object(raw))
    assert got["reason"] == "the } is literal" and got["a"]["b"] == 1


@pytest.mark.parametrize("raw", ["no json here", '{"unbalanced": '])
def test_unparseable_replies_raise_rather_than_guess(raw):
    with pytest.raises(LLMError):
        _first_json_object(raw)


# --- the degradation ladder ----------------------------------------------
def _fake_post(monkeypatch, responses):
    """responses: list of (status_code, body) consumed in order."""
    calls: list[dict] = []

    class R:
        def __init__(self, code, body):
            self.status_code, self._b = code, body
        @property
        def text(self): return json.dumps(self._b)
        def json(self): return self._b

    def post(url, headers=None, json=None, timeout=None):
        calls.append(json)
        return R(*responses[len(calls) - 1])

    monkeypatch.setattr(llm.httpx, "post", post)
    return calls


def _ok(content):
    return 200, {"choices": [{"message": {"content": content}}]}


def test_falls_back_to_json_mode_when_strict_schema_is_rejected(monkeypatch):
    calls = _fake_post(monkeypatch, [
        (400, {"error": "response_format json_schema not supported"}),
        _ok('{"veto": false, "reason": "fine"}'),
    ])
    got = OpenAICompatProvider("fw-x").parse("sys", "user", Toy)

    assert got.veto is False
    assert calls[0]["response_format"]["type"] == "json_schema"
    assert calls[1]["response_format"]["type"] == "json_object"
    assert "JSON Schema" in calls[1]["messages"][0]["content"], \
        "the schema must survive into the prompt once the endpoint drops it"


def test_reasks_once_carrying_the_validation_error_back(monkeypatch):
    calls = _fake_post(monkeypatch, [
        _ok('{"veto": "not a bool", "reason": "x"}'),
        _ok('{"veto": "still wrong"}'),
        _ok('{"veto": true, "reason": "third time"}'),
    ])
    got = OpenAICompatProvider("fw-x").parse("sys", "user", Toy)

    assert got.veto is True and len(calls) == 3
    assert "did not validate" in calls[2]["messages"][-1]["content"]


def test_exhausting_the_ladder_raises_so_the_caller_fails_closed(monkeypatch):
    _fake_post(monkeypatch, [_ok("garbage")] * 3)
    with pytest.raises(LLMError):
        OpenAICompatProvider("fw-x").parse("sys", "user", Toy)


def test_gated_model_says_what_to_click_and_does_not_burn_retries(monkeypatch):
    calls = _fake_post(monkeypatch, [(403, {"error": "gated"})])
    with pytest.raises(LLMError, match="Unlock Model"):
        OpenAICompatProvider("fw-x").parse("sys", "user", Toy)
    assert len(calls) == 1, "a licence click is not a transient failure"


# --- which brain answers -------------------------------------------------
FW, AN = {"FEATHERLESS_API_KEY": "fw-x"}, {"ANTHROPIC_API_KEY": "sk-x"}


def test_featherless_wins_by_default_because_it_is_the_funded_path():
    assert isinstance(build_provider({**FW, **AN}), OpenAICompatProvider)


def test_anthropic_is_used_when_it_is_the_only_key():
    assert isinstance(build_provider(dict(AN)), AnthropicProvider)


@pytest.mark.parametrize("env,want", [
    ({}, None),
    ({"CONTOUR_LLM": "off", **FW, **AN}, None),
    ({"CONTOUR_LLM": "anthropic", **FW, **AN}, AnthropicProvider),
    ({"CONTOUR_LLM": "featherless", **FW, **AN}, OpenAICompatProvider),
    ({"CONTOUR_LLM": "featherless", **AN}, None),
])
def test_explicit_choice_overrides_and_missing_key_means_degraded(env, want):
    got = build_provider(env)
    assert got is None if want is None else isinstance(got, want)


# --- the no-card fallback ------------------------------------------------
GM = {"GEMINI_API_KEY": "AIza-x"}


def test_gemini_is_used_when_featherless_is_unavailable():
    """The card wall on Featherless is the reason this seam exists."""
    got = build_provider({**GM, **AN})
    assert isinstance(got, OpenAICompatProvider)
    assert "generativelanguage" in got.base_url and got.model.startswith("gemini")


def test_google_api_key_is_accepted_as_an_alias():
    assert isinstance(build_provider({"GOOGLE_API_KEY": "AIza-x"}),
                      OpenAICompatProvider)


def test_preference_order_is_featherless_then_gemini_then_anthropic():
    assert build_provider({**FW, **GM, **AN}).base_url.startswith(
        "https://api.featherless.ai")
    assert "generativelanguage" in build_provider({**GM, **AN}).base_url
    assert isinstance(build_provider(dict(AN)), AnthropicProvider)


def test_model_override_survives_provider_selection():
    """If a model id turns out wrong we fix it with an env var, not a deploy."""
    got = build_provider({**GM, "CONTOUR_LLM_MODEL": "gemini-2.5-flash"})
    assert got.model == "gemini-2.5-flash"


def test_explicit_gemini_choice_ignores_a_present_featherless_key():
    got = build_provider({**FW, **GM, "CONTOUR_LLM": "gemini"})
    assert "generativelanguage" in got.base_url


# --- Bedrock: Claude, on credits that actually exist ---------------------
BR = {"AWS_ACCESS_KEY_ID": "AKIA-x", "AWS_SECRET_ACCESS_KEY": "s3cr3t"}


def test_bedrock_wins_when_configured_because_its_credits_exist():
    got = build_provider({**BR, **FW, **GM, **AN})
    assert isinstance(got, BedrockProvider)
    assert got.model.startswith("us.anthropic.claude-haiku-4-5"), \
        "measured: the newer ids 403 as unentitled on this account"


def test_bedrock_accepts_a_bearer_token_instead_of_a_key_pair():
    got = build_provider({"AWS_BEARER_TOKEN_BEDROCK": "bt-x"})
    assert isinstance(got, BedrockProvider) and got.api_key == "bt-x"


def test_a_lone_access_key_is_not_enough_to_claim_a_brain():
    assert build_provider({"AWS_ACCESS_KEY_ID": "AKIA-x"}) is None


def test_region_falls_back_through_the_usual_aws_names():
    assert build_provider({**BR}).region == "us-east-1"
    assert build_provider({**BR, "AWS_DEFAULT_REGION": "ap-south-1"}).region == "ap-south-1"
    assert build_provider({**BR, "AWS_REGION": "eu-west-1"}).region == "eu-west-1"


def test_bedrock_carries_the_schema_in_the_prompt_and_reasks_once(monkeypatch):
    """Bedrock supports no structured output at all, so the schema has to
    travel in the system prompt and be validated here."""
    seen: list[tuple[str, list[dict]]] = []
    replies = iter(['{"veto": "nope"}', '{"veto": true, "reason": "second try"}'])

    p = BedrockProvider()

    def fake_invoke(system, msgs, max_tokens):
        seen.append((system, msgs))
        return next(replies)

    monkeypatch.setattr(p, "_invoke", fake_invoke)
    got = p.parse("sys", "user", Toy)

    assert got.veto is True and len(seen) == 2
    assert "JSON Schema" in seen[0][0], "schema must be in the system prompt"
    assert "did not validate" in seen[1][1][-1]["content"]
    assert seen[1][1][-2]["role"] == "assistant", \
        "the model must see its own failed reply, not just the complaint"


def test_bedrock_names_the_payment_problem_rather_than_the_status_code(monkeypatch):
    """INVALID_PAYMENT_INSTRUMENT is an entitlement fact, not a transient 403.
    Measured on the real account: Sonnet 4.5 returns it, Haiku 4.5 does not."""
    class R:
        status_code = 403
        text = '{"message":"Model access is denied due to INVALID_PAYMENT_INSTRUMENT:A valid..."}'

    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: R())
    with pytest.raises(LLMError, match="needs a payment method"):
        BedrockProvider(api_key="bt-x").parse("sys", "user", Toy)


def test_key_pair_without_the_bedrock_extra_says_what_to_install(monkeypatch):
    import builtins
    real = builtins.__import__

    def no_bedrock(name, *a, **k):
        if name == "anthropic":
            raise ImportError("no boto3")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_bedrock)
    with pytest.raises(LLMError, match=r"anthropic\[bedrock\]"):
        BedrockProvider(access_key="AKIA", secret_key="s").parse("s", "u", Toy)

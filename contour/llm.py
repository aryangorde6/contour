"""Provider seam for the advisory layer.

The agent's brain is a config value, not an architecture. This module is the
only place that knows which vendor is answering, so mind.py can state the
policy -- what the model is allowed to decide, and what happens when it fails
-- without also carrying the mechanics of who it is talking to.

Two providers, one contract: given a system prompt, a user prompt and a
pydantic model, return a validated instance of that model or raise. Everything
above this line treats a raise as "the brain failed", which is a signal the
failure policy in mind.py already knows how to act on.

Featherless is an OpenAI-compatible endpoint serving open-weight models. It
does not guarantee the strict json_schema response format, so OpenAICompat
degrades in three stages: strict schema, then plain JSON mode with the schema
inlined in the prompt, then a re-ask carrying the validation error back. If all
three fail we raise, and the caller fails closed. That is the correct outcome:
a brain that cannot answer on-schema is not a brain we should be trading on.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ValidationError

TIMEOUT = 90.0

# Featherless' own recommendation in the ALPACA26 setup guide. Any model in
# their catalogue works; this one is provisioned warm, which matters when the
# whole call has to finish inside a 15-minute cron cycle.
FEATHERLESS_BASE = "https://api.featherless.ai/v1"
FEATHERLESS_MODEL = "zai-org/GLM-5.2"

# Google AI Studio speaks the same OpenAI shape, and its free tier needs no
# card -- which is the whole reason this seam exists. 15 RPM / 1500 RPD against
# our ~29 calls a day is not a constraint worth designing around.
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"
GEMINI_MODEL = "gemini-3.7-flash"

# Sonnet 5 is Open access on Bedrock; Opus 5 is gated behind an access
# request. $2/$10 per MTok makes a week of this agent cost about a dollar.
BEDROCK_MODEL = "anthropic.claude-sonnet-5"

ANTHROPIC_MODEL = "claude-opus-5"


class LLMError(RuntimeError):
    """Any failure to obtain a schema-valid answer. Callers fail closed."""


class Provider(Protocol):
    name: str
    model: str

    def parse(self, system: str, user: str, schema: type[BaseModel],
              effort: str = "low") -> BaseModel: ...


def _first_json_object(text: str) -> str:
    """Pull the outermost {...} out of a reply.

    Open-weight models fence their JSON, prefix it with reasoning, or both.
    Brace-matching beats a regex here because the payloads nest.
    """
    text = re.sub(r"^\s*```(?:json)?|```\s*$", "", text.strip(), flags=re.M)
    start = text.find("{")
    if start < 0:
        raise LLMError(f"no JSON object in reply: {text[:200]!r}")
    depth, in_str, esc = 0, False, False
    for i, ch in enumerate(text[start:], start):
        if esc:
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == '"':
            in_str = not in_str
        elif not in_str:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
    raise LLMError(f"unbalanced JSON in reply: {text[:200]!r}")


def _schema_instruction(schema: type[BaseModel]) -> str:
    """For endpoints with no native structured output. Amazon Bedrock is one:
    it serves the Messages API but explicitly does not support structured
    outputs, so the schema has to travel in the prompt."""
    return ("Reply with a single JSON object and nothing else -- no prose, no "
            "code fence. It must validate against this JSON Schema:\n"
            + json.dumps(schema.model_json_schema()))


class AnthropicProvider:
    """First-party path. Kept whole: if credits ever appear, this is a
    one-line switch, not a rewrite."""

    name = "anthropic"

    def __init__(self, api_key: str, model: str = ANTHROPIC_MODEL):
        self.api_key, self.model = api_key, model
        self._client: Any = None

    def _c(self) -> Any:
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client

    def parse(self, system: str, user: str, schema: type[BaseModel],
              effort: str = "low") -> BaseModel:
        r = self._c().messages.parse(
            model=self.model, max_tokens=8000,
            output_config={"effort": effort},
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=schema,
        )
        return r.parsed_output


class OpenAICompatProvider:
    """Featherless, or anything else speaking /v1/chat/completions."""

    name = "openai_compat"

    def __init__(self, api_key: str, base_url: str = FEATHERLESS_BASE,
                 model: str = FEATHERLESS_MODEL):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model

    def _post(self, body: dict) -> str:
        r = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json"},
            json=body, timeout=TIMEOUT,
        )
        if r.status_code == 403:
            raise LLMError(
                f"403 on {self.model}: the model is gated. Open its page on "
                f"featherless.ai and click 'Unlock Model' to accept the licence."
            )
        if r.status_code >= 400:
            raise LLMError(f"HTTP {r.status_code}: {r.text[:300]}")
        try:
            return r.json()["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMError(f"malformed response: {type(exc).__name__}: {exc}")

    def parse(self, system: str, user: str, schema: type[BaseModel],
              effort: str = "low") -> BaseModel:
        js = schema.model_json_schema()
        base = {"model": self.model, "max_tokens": 4000, "temperature": 0.2}

        attempts: list[dict] = [
            # 1. strict schema, if the endpoint honours it
            {**base, "messages": [{"role": "system", "content": system},
                                  {"role": "user", "content": user}],
             "response_format": {"type": "json_schema", "json_schema": {
                 "name": schema.__name__, "schema": js, "strict": True}}},
            # 2. plain JSON mode, schema carried in the prompt instead
            {**base, "messages": [
                {"role": "system", "content":
                    f"{system}\n\nReply with a single JSON object and nothing "
                    f"else. It must validate against this JSON Schema:\n"
                    f"{json.dumps(js)}"},
                {"role": "user", "content": user}],
             "response_format": {"type": "json_object"}},
        ]

        last = ""
        for body in attempts:
            try:
                raw = self._post(body)
                return schema.model_validate_json(_first_json_object(raw))
            except (LLMError, ValidationError, json.JSONDecodeError) as exc:
                last = f"{type(exc).__name__}: {exc}"
                if isinstance(exc, LLMError) and "gated" in str(exc):
                    raise                       # a licence click, not a retry

        # 3. one re-ask carrying the failure back to the model
        body = {**attempts[1]}
        body["messages"] = list(body["messages"]) + [
            {"role": "user", "content":
                f"Your previous reply did not validate: {last}. Return only "
                f"the JSON object, matching the schema exactly."}]
        raw = self._post(body)
        return schema.model_validate_json(_first_json_object(raw))


class BedrockProvider:
    """Claude through Amazon Bedrock.

    Same Messages API shape as first-party, with two differences that matter
    here: structured outputs are NOT supported, so the schema goes in the
    prompt and we validate ourselves; and auth is either a bearer token (short
    lived, 12h) or an AWS key pair (long lived, which is what a four-day cron
    actually needs).

    Default model is Sonnet 5 rather than Opus 5 deliberately: Sonnet is Open
    access on Bedrock while Opus 5 is gated, and for a job that returns three
    small fixed-shape objects the difference is not worth an access request.
    """

    name = "bedrock"

    def __init__(self, model: str = BEDROCK_MODEL, region: str = "us-east-1",
                 api_key: str = "", access_key: str = "", secret_key: str = "",
                 session_token: str = ""):
        self.model, self.region = model, region
        self.api_key = api_key
        self.access_key, self.secret_key = access_key, secret_key
        self.session_token = session_token
        self._client: Any = None

    def _c(self) -> Any:
        if self._client is None:
            from anthropic import AnthropicBedrockMantle
            if self.api_key:
                self._client = AnthropicBedrockMantle(
                    api_key=self.api_key, aws_region=self.region)
            else:
                self._client = AnthropicBedrockMantle(
                    aws_access_key=self.access_key,
                    aws_secret_key=self.secret_key,
                    aws_session_token=self.session_token or None,
                    aws_region=self.region)
        return self._client

    def parse(self, system: str, user: str, schema: type[BaseModel],
              effort: str = "low") -> BaseModel:
        sys_p = f"{system}\n\n{_schema_instruction(schema)}"
        msgs: list[dict] = [{"role": "user", "content": user}]
        last = ""
        for _ in range(2):
            try:
                r = self._c().messages.create(
                    model=self.model, max_tokens=4000,
                    system=sys_p, messages=msgs)
                raw = "".join(b.text for b in r.content
                              if getattr(b, "type", None) == "text")
            except Exception as exc:                            # noqa: BLE001
                raise LLMError(f"{type(exc).__name__}: {exc}") from exc
            try:
                return schema.model_validate_json(_first_json_object(raw))
            except (LLMError, ValidationError) as exc:
                last = f"{type(exc).__name__}: {exc}"
                msgs = msgs + [
                    {"role": "assistant", "content": raw or "(empty)"},
                    {"role": "user", "content":
                        f"That did not validate: {last}. Return only the JSON "
                        f"object, matching the schema exactly."}]
        raise LLMError(f"bedrock returned nothing schema-valid: {last}")


def build_provider(env: dict[str, str] | None = None) -> Provider | None:
    """Pick a brain from the environment. None means run degraded.

    Featherless wins by default because it is the funded path for this
    hackathon; CONTOUR_LLM overrides for testing and for the write-up's
    reproducibility claim.
    """
    e = os.environ if env is None else env
    choice = (e.get("CONTOUR_LLM") or "").strip().lower()
    override = (e.get("CONTOUR_LLM_MODEL") or "").strip()
    bedrock_key = (e.get("AWS_BEARER_TOKEN_BEDROCK", "")
                   or e.get("BEDROCK_API_KEY", ""))
    ak, sk = e.get("AWS_ACCESS_KEY_ID", ""), e.get("AWS_SECRET_ACCESS_KEY", "")
    region = e.get("AWS_REGION", "") or e.get("AWS_DEFAULT_REGION", "") or "us-east-1"
    fw = e.get("FEATHERLESS_API_KEY", "")
    gm = e.get("GEMINI_API_KEY", "") or e.get("GOOGLE_API_KEY", "")
    an = e.get("ANTHROPIC_API_KEY", "")

    def bedrock():
        if not (bedrock_key or (ak and sk)):
            return None
        return BedrockProvider(
            model=override or e.get("BEDROCK_MODEL", "") or BEDROCK_MODEL,
            region=region, api_key=bedrock_key, access_key=ak, secret_key=sk,
            session_token=e.get("AWS_SESSION_TOKEN", ""))

    def feather():
        return OpenAICompatProvider(
            fw, FEATHERLESS_BASE, override or FEATHERLESS_MODEL) if fw else None

    def gemini():
        return OpenAICompatProvider(
            gm, GEMINI_BASE, override or GEMINI_MODEL) if gm else None

    def claude():
        return AnthropicProvider(an, override or ANTHROPIC_MODEL) if an else None

    if choice == "off":
        return None
    if choice in ("bedrock", "featherless", "gemini", "anthropic"):
        return {"bedrock": bedrock, "featherless": feather, "gemini": gemini,
                "anthropic": claude}[choice]()
    # Preference order is availability, not taste. Bedrock first: it is the
    # only path where we have both a real Claude and credits that exist.
    # Featherless needs a card we do not have, Gemini is the free fallback,
    # first-party Anthropic has no credits at all.
    return bedrock() or feather() or gemini() or claude()

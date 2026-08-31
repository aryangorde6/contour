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


def build_provider(env: dict[str, str] | None = None) -> Provider | None:
    """Pick a brain from the environment. None means run degraded.

    Featherless wins by default because it is the funded path for this
    hackathon; CONTOUR_LLM overrides for testing and for the write-up's
    reproducibility claim.
    """
    e = os.environ if env is None else env
    choice = (e.get("CONTOUR_LLM") or "").strip().lower()
    override = (e.get("CONTOUR_LLM_MODEL") or "").strip()
    fw = e.get("FEATHERLESS_API_KEY", "")
    gm = e.get("GEMINI_API_KEY", "") or e.get("GOOGLE_API_KEY", "")
    an = e.get("ANTHROPIC_API_KEY", "")

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
    if choice in ("featherless", "gemini", "anthropic"):
        return {"featherless": feather, "gemini": gemini,
                "anthropic": claude}[choice]()
    # Preference order is availability, not quality: Featherless is the funded
    # partner path, Gemini the no-card fallback, Anthropic needs credits we
    # do not have.
    return feather() or gemini() or claude()

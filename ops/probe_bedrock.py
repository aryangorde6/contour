"""What can this AWS account actually call?

The Bedrock catalogue lists models the account has no entitlement to, so
ListFoundationModels answers the wrong question. This asks the right one by
sending a real request to every candidate and reporting what comes back.

Uses the Converse API rather than per-provider invoke schemas, because Converse
is one body shape across Nova, Llama, Qwen, Mistral, DeepSeek and GPT-OSS --
which is also why the agent should speak it if any of them work.

    python ops/probe_bedrock.py [region ...]
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx

REGIONS = sys.argv[1:] or ["us-east-1", "ap-south-1"]
# Region prefixes for models that require a cross-region inference profile.
PREFIX = {"us-east-1": "us.", "us-west-2": "us.", "ap-south-1": "apac.",
          "eu-west-1": "eu."}
PROMPT = 'Return exactly this JSON and nothing else: {"veto": false, "reason": "probe"}'


def load_env() -> str:
    for line in Path(".env").read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            if v.strip():
                os.environ.setdefault(k.strip(), v.strip())
    tok = os.environ.get("AWS_BEARER_TOKEN_BEDROCK", "")
    if not tok:
        sys.exit("AWS_BEARER_TOKEN_BEDROCK is empty in .env -- paste it back first")
    return tok


def catalogue(region: str, h: dict) -> list[dict]:
    r = httpx.get(f"https://bedrock.{region}.amazonaws.com/foundation-models",
                  headers=h, timeout=45)
    r.raise_for_status()
    return [m for m in r.json().get("modelSummaries", [])
            if "TEXT" in m.get("outputModalities", [])
            and "TEXT" in m.get("inputModalities", [])
            and set(m.get("inferenceTypesSupported", []))
            & {"ON_DEMAND", "INFERENCE_PROFILE"}]


def converse(region: str, mid: str, h: dict) -> tuple[int, str]:
    url = f"https://bedrock-runtime.{region}.amazonaws.com/model/{mid}/converse"
    body = {"messages": [{"role": "user", "content": [{"text": PROMPT}]}],
            "inferenceConfig": {"maxTokens": 64, "temperature": 0.2}}
    try:
        r = httpx.post(url, headers=h, json=body, timeout=60)
    except Exception as exc:                                    # noqa: BLE001
        return 0, f"{type(exc).__name__}: {exc}"[:90]
    if r.status_code == 200:
        try:
            blocks = r.json()["output"]["message"]["content"]
            return 200, "".join(b.get("text", "") for b in blocks).strip()[:70]
        except (KeyError, ValueError) as exc:
            return 200, f"(unparsed: {exc})"
    try:
        msg = r.json().get("message") or r.json().get("Message") or r.text
    except ValueError:
        msg = r.text
    return r.status_code, str(msg)[:90]


def main() -> int:
    tok = load_env()
    h = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
    working: list[tuple[str, str, str]] = []

    for region in REGIONS:
        try:
            models = catalogue(region, h)
        except Exception as exc:                                # noqa: BLE001
            print(f"\n### {region}: catalogue failed -- {exc}")
            continue

        # One id per family: probing every point release burns time for nothing.
        seen: set[str] = set()
        cands: list[str] = []
        for m in sorted(models, key=lambda m: m["modelId"]):
            mid, prov = m["modelId"], m["modelId"].split(".")[0]
            if prov == "anthropic":
                continue                     # measured: 403 payment instrument
            fam = ".".join(mid.split(":")[0].split("-")[:3])
            if fam in seen:
                continue
            seen.add(fam)
            profile_only = "ON_DEMAND" not in m.get("inferenceTypesSupported", [])
            cands.append(PREFIX.get(region, "") + mid if profile_only else mid)

        print(f"\n### {region}: probing {len(cands)} model families")
        for mid in cands:
            code, msg = converse(region, mid, h)
            flag = "OK  " if code == 200 else f"{code:<4}"
            print(f"  {flag} {mid:<58} {msg}")
            if code == 200:
                working.append((region, mid, msg))

    print("\n" + "=" * 78)
    if not working:
        print("NOTHING CALLABLE on this account.")
        return 1
    print(f"CALLABLE ({len(working)}):")
    for region, mid, sample in working:
        print(f"  {region:<11} {mid:<58} -> {sample}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

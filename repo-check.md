# Contour repo check — `aryangorde6/contour`

**Date:** 2026-09-01  
**Scope:** Public GitHub repo [aryangorde6/contour](https://github.com/aryangorde6/contour) (local clone `/home/aryan/alpaca_hack`, `origin` matches).  
**Method:** Read-only review of claims vs code, secrets/CI exposure, trading-path safety, and dashboard integrity.  
**No code was changed** as part of this review (this file only).

**Verdict in one line:** The engineering is unusually careful for a hackathon repo; most big claims hold under pytest and source inspection. The main gaps are **stale/overreaching public claims (especially MCP on the cover)**, **inherent CI secret-exfil risk if `main` is compromised**, and a few **documented-but-real trading/ops failure modes**.

---

## 1. Repo snapshot

| Item | Value |
|---|---|
| Visibility | Public, MIT |
| Default branch | `main` |
| Companion branch | `agent-state` (live journal + published state; exists on origin) |
| Stack | Python 3.12, `alpaca-py`, Alpaca CLI subprocess, optional Bedrock/Gemini/Featherless |
| Purpose | Alpaca AI Trading Agents Hackathon (28 Aug – 4 Sep 2026), paper options agent “Contour” |
| Local pytest | **223 passed** (2026-09-01) |

Tracked surface area of note: `contour/` agent, `dashboard/`, `.github/workflows/{agent,ci,fixture,pages}.yml`, `WRITEUP.md` / `TECHNICAL.md` / `state.md`, committed `ACCOUNT_ID.txt`, fixtures under `fixtures/`.

---

## 2. Claims audit

### 2.1 Verified true (code / tests back them)

| Claim | Evidence |
|---|---|
| **223 tests** | README/WRITEUP/state.md; `pytest --collect-only` → 223; full run **223 passed**. |
| **Options gates G1–G12, pure, fixed order, zero I/O** | `contour/gates.py` defines `g1`…`g12` and `GATES` tuple; `evaluate()` short-circuits on first fail but journals reasons via callers. |
| **Sleeve gates S1–S7** | `contour/sleeve.py` (`s1`…`s7`); **19 gates total** when counting both books. |
| **Universe SPY / QQQ / IWM, $100k start NAV, expiry 2026-09-11** | `contour/config.py` (`UNIVERSE`, `START_NAV`, `EXPIRY`). |
| **Structure map from VRP + 25Δ skew** | `WRITEUP.md` table; `contour/select.py` + `surface.py` implement measurement → structure. |
| **Market reads via `alpaca-py`, not MCP** | `contour/data.py` (`OptionHistoricalDataClient`, `StockHistoricalDataClient`, `TradingClient`); no MCP client import anywhere in `contour/`. |
| **Orders via Alpaca CLI multi-leg** | `contour/execute.py` `CLIBroker.submit_mleg` → `subprocess.run([cli, "order", "submit", "--order-class", "mleg", ...])`. |
| **Account assertion before orders** | `CLIBroker.assert_account()` compares broker `account_number` to expected; `__main__.py` loads judged ID from `ACCOUNT_ID.txt`. |
| **`--dev` refuses judged account** | `__main__.py` returns exit 3 if `--dev` credentials resolve to committed judged ID. |
| **LLM cannot place orders structurally** | `execute.py` does not import `mind`; wiring is blackouts / stand-down / veto only into `loop.py`. |
| **Sizing is `regime.py`, not the model** | `loop.py` journals `multiplier_role: "stand-down only -- sizing is regime.py"`; LLM `multiplier == 0` stands the book down. |
| **Degraded brain (no provider) halves size** | `brain_floor` / `DEGRADED_BRAIN_SIZE` path in `loop.py` + `config.py`. |
| **Append-only SHA-256 journal** | `contour/journal.py` `link_hash` / `verify()`. |
| **Dashboard re-verifies chain in-browser** | `dashboard/index.html` uses `crypto.subtle.digest("SHA-256", …)` against `agent-state` raw files; `tests/test_dashboard.py` cross-checks JS vs Python (incl. tamper case). |
| **15-minute autonomy via Actions** | `.github/workflows/agent.yml` cron `*/15 14-19 * * 1-5` plus pre-open and Thursday escalate. |
| **No PR trigger on secretful workflows** | `agent.yml` / `fixture.yml` are `schedule` + `workflow_dispatch` only; comments explicitly ban `pull_request`. |
| **CI has no trading secrets** | `.github/workflows/ci.yml` `permissions: contents: read`; runs pytest, `--replay`, `--verify`, grep-blocks `close-all` / `cancel-all`. |
| **Paper TradingClient for SDK clock/trading reads** | `data.py` and `__main__._market_open` use `TradingClient(..., paper=True)`. |
| **`--replay` needs no credentials** | `ci.yml` asserts this; `contour/replay.py` + committed fixtures. |
| **Dual dashboard hosting** | `pages.yml` → GitHub Pages; `vercel.json` serves `dashboard/`. |
| **Kill switch / unique `client_order_id`** | G12 / S7 on `HALT` file + seen IDs. |

### 2.2 False, stale, or misleading claims

| Claim / surface | Reality | Severity for judging |
|---|---|---|
| **Cover art: “Alpaca Trading API · MCP · CLI …”** (`assets/cover.svg`, `assets/make_cover.py`, `contour/cover.png`) | There is **no MCP client** in the package. Docs elsewhere correctly say MCP was tried and blocked (`alpaca-mcp-server#97`); contest rule is “MCP **and/or** CLI”. Cover still **implies MCP is part of the running stack**. | **High** (visual judges / social / deck) |
| **README status checkbox: ``gates.py` — G1–G12, twelve pure functions``** only | True for that file, but easy to read as “the agent has twelve gates.” WRITEUP/README prose later say twelve + seven; cover says **19**. Status block is incomplete vs current product. | Medium (consistency) |
| **`state.md` mind row: “regime multiplier, structure veto”** as if multiplier still sizes | Multiplier remains in `mind.py` / journal, but **sizing moved to trend systems**; multiplier is stand-down / degraded-floor. Row is outdated wording. | Low–Medium |
| **`mind.py` module docstring still leads with “size multiplier bounded at 1.0”** | Accurate that outputs are capped ≤1, but oversells current role vs `loop.py` comments (“advisory layer no longer SIZES”). | Low |
| **Older narrative that reads go “through MCP”** | Corrected in README / WRITEUP / TECHNICAL / `state.md` evidence table. Residual MCP branding is the cover (and any external posts that still say it). | Covered above |
| **Ops video script still stages “twelve green gates” as the climax** (`ops/video.md`) | Still true for the options book demo, but undersells the sleeve’s seven and the “19” line used elsewhere. | Low (presentation) |

### 2.3 Overstated but defensible

- **“Autonomous”**: Real unattended cron exists; still depends on GitHub Actions availability, API keys in repo secrets, LLM provider uptime (degrades rather than dies), and indicative free options data quality.
- **“The measurement picks the structure”**: True for the four-branch map; thresholds, priors, and VRP floor are human-chosen constants in `config.py`.
- **LRS-Fortress / Sharpe 0.94 citation**: WRITEUP is careful that only sizing is inherited, not the gold sleeve / full system — still a marketing-adjacent citation judges may challenge.
- **Hash-chain “tamper-evident”**: Protects integrity of the published log; **whoever controls `agent-state` push credentials can append valid new links**. It is not a third-party attestation of P&L.
- **MCP issue #97 as a “finding”**: Documented and consistent with CLI choice; this review did not re-verify the upstream bug live.

### 2.4 Important behavior that is real but easy to miss

- Scheduled `agent.yml` runs **without** `--dry` / `--dev` (manual dispatch defaults those to true). Cron is the live judged path.
- `assert_account()` is **cached after first success per process** (`_verified`) — one check per cycle, not per order.
- Sleeve is **one-shot** (`SLEEVE_ONE_SHOT`); stop is broker GTC; options book has **no resting multi-leg stop** (polling `manage.py`).
- Free Alpaca options feed is treated as **indicative**; fills vs decision mid may diverge (`state.md` evidence table).
- `ops/repair_book.py` exists because early fills were **not persisted** — unmanaged book was a real production bug, later patched and documented.

---

## 3. Security findings

Severity scale: **Critical / High / Medium / Low / Info**.

### 3.1 Critical

*None observed in the committed tree.* No Alpaca secret values, AWS keys, or Bearer tokens found in tracked source. `.env` is gitignored and present only locally. `.env.example` is empty placeholders.

### 3.2 High

| ID | Finding | Evidence / scenario |
|---|---|---|
| H1 | **Secretful workflow on public `main` with `contents: write`** | `agent.yml` injects Alpaca + LLM/AWS secrets and can push `agent-state`. There is **no `pull_request` trigger** (good), but **any commit that lands on `main`** (compromised maintainer token, malicious merge, supply-chain PR that gets approved) can read `${{ secrets.* }}` and exfiltrate. Inherent to “Actions trades for us.” |
| H2 | **Cover / branding claims MCP in use** | Not a credential leak; it is a **contest-compliance / honesty** risk if reviewers treat the cover as the architecture. |

### 3.3 Medium

| ID | Finding | Evidence / scenario |
|---|---|---|
| M1 | **Judged paper account ID is public by design** | `ACCOUNT_ID.txt` = `PA35XVXLIO0E`; repeated in WRITEUP, dashboard, submission docs. Enables third parties to watch the paper account / copy strategy timing from `agent-state`. Acceptable for a judged hackathon; still **information disclosure**. |
| M2 | **Dev account ID also published** | `state.md` lists `PA35MRNGUR91`. Widens fingerprinting of the throwaway account. |
| M3 | **CLI path does not hard-pin paper base URL** | SDK uses `paper=True`; CLI relies on **key type** + **account_number assert**. Wrong live keys that somehow matched a rewritten `ACCOUNT_ID.txt` could trade live. Mitigations exist; defense is configuration hygiene, not a hard “paper-only” CLI flag. |
| M4 | **Dashboard `innerHTML` + partial HTML escaping** | `esc()` escapes `& < >` only (`dashboard/index.html`), not quotes. Most injected fields go through `esc()`; journal/`agent-state` is **attacker-controlled if publish credentials leak**. Residual XSS risk is low given same-origin static host + trusted branch, but not bulletproof. |
| M5 | **`fixture.yml` can commit to `main` with write permission** | Uses only `ALPACA_DEV_*` and `--dry --dev`, but still a secretful write path to the judged codebase. Same “don’t add PR triggers” discipline applies. |
| M6 | **Public `agent-state` is the live audit trail** | By design for the dashboard; also leaks full decision/gate/fill narrative to competitors. |

### 3.4 Low

| ID | Finding | Notes |
|---|---|---|
| L1 | **`go install …/alpaca@latest`** | Supply-chain drift vs pinned cache key `alpaca-cli-v0.0.14`; cache hit may not match “latest.” |
| L2 | **Maintainer email in agent commit identity** | `aryangorde6@gmail.com` in publish step — minor PII. |
| L3 | **`ops/repair_book.py --write`** | Can rewrite local `state/positions.json` from broker history; dangerous if mis-run against wrong env, but paper API hardcoded. |
| L4 | **Local `.env` on disk** | Correctly ignored; still a laptop compromise risk (standard). |

### 3.5 Info / strengths (do not “fix”; keep)

- No `shell=True` / `os.system` / `eval` in agent path; CLI args are a fixed argv list + JSON legs blob.
- CI grep ban on account-wide `close-all` / `cancel-all`.
- Concurrency group on agent prevents overlapping writers forking the hash chain.
- Verify-before-publish; refuse empty tree publish.
- Manual workflow_dispatch defaults `dry=true`, `dev=true`.
- Structural LLM isolation from `execute.py`.
- Replay + credential-free import checks in CI.
- Pages workflow given no trading secrets; blocks root-relative URLs that would break `/contour/` project pages.

---

## 4. Trading / correctness risks (not classic “CVE”, still material)

| Risk | Status |
|---|---|
| Unmanaged fills if state publish fails after submit | Previously bitten; mitigated with longer timeout, always-publish-on-failure, positions in `state/`, sleeve adopt-orphan logic + tests. Residual: job kill after fill still painful. |
| Partial / unbalanced multi-leg paper fills | `reconcile()` exists; loop can halt new entries if book risk uncomputable. |
| No resting stop on multi-leg options | Documented; exits are polled; Thursday flatten + market escalation cron. |
| Indicative greeks/quotes vs NBBO fills | Documented honesty gap; can make gates/selection disagree with fills. |
| `assert_account` cache | Fine within one cycle; not a cross-credential hot-swap defense. |
| Cron late under Actions load | Documented; phase from market clock, not fire time. |
| LLM fail-closed can zero the book for a cycle | Intended; degraded (absent key) still trades smaller. |

---

## 5. Docs / product consistency scorecard

| Area | Grade | Note |
|---|---|---|
| Core architecture (CLI write, SDK read, gates, journal) | **A** | Matches code |
| Gate count messaging | **B** | 12 vs 19 vs “twelve + seven” depending on surface |
| MCP messaging | **C** | Prose fixed; **cover still wrong** |
| AI / sizing story | **A−** | WRITEUP matches `loop.py`; `mind.py` / `state.md` slightly behind |
| Test count claim | **A** | 223/223 |
| Security posture for a public trading bot | **A−** | Strong workflow hygiene; inherent Actions secret risk remains |
| Ops honesty (`state.md` postmortems) | **A** | Unusually candid; good for judges, also exposes past failures |

---

## 6. Contest-alignment checklist (hackathon rules)

| Requirement | Contour |
|---|---|
| Autonomous agent | Yes (Actions cron) |
| Alpaca Trading API | Yes (`alpaca-py`) |
| MCP **and/or** CLI | **CLI yes**; MCP not used at runtime (allowed by “or”) |
| Options in strategy | Yes (credit spreads / condors) + equity sleeve |
| Dedicated paper account @ $100k | Claimed `PA35XVXLIO0E`; `verify_setup.py` enforces $100k when run |
| One-page write-up | `WRITEUP.md` present |
| Do not over-claim MCP as “used” | Submission notes say don’t tag MCP; **cover still does** |

---

## 7. Recommended fixes (advisory only — not applied)

Ordered by leverage for honesty / risk reduction:

1. **Remove “MCP” from cover / deck hero lines** (or change to “MCP attempted · CLI for multi-leg”) so public art matches README.
2. **Normalize gate count** in README status checkbox and video script to “G1–G12 + S1–S7 (19)” everywhere.
3. **Refresh `state.md` mind bullet** to “stand-down / veto / blackouts” instead of sizing multiplier.
4. Soften or update `mind.py` top docstring to match stand-down-only sizing role.
5. Consider pinning Alpaca CLI version in install (not only cache key).
6. Optional: escape quotes in `esc()` for defense-in-depth on dashboard.
7. Keep never adding `pull_request` / `pull_request_target` to `agent.yml` or `fixture.yml`.

---

## 8. Bottom line

Contour’s **implementation claims are largely earned**: 223 passing tests, real CLI multi-leg execution with account assertions, deterministic gates, hash-chained journal with browser re-verify, and careful CI secret boundaries. The sharpest problems for a public judged submission are **claim hygiene (MCP on the cover; twelve-vs-nineteen inconsistency)** and the **unavoidable “Actions holds live trading secrets on a public repo”** threat model — mitigated well against forks, not against a compromised `main`.

This file is an audit snapshot, not a guarantee against future commits.

# Contour — project state

Working log for continuity. Written 2026-08-31. Update it when a decision
changes, not when a line of code changes.

---

## 1. The deadline

| | |
|---|---|
| Event | Alpaca AI Trading Agents Hackathon (lablab.ai × Alpaca) |
| **Submission deadline** | **2026-09-04 15:00 UTC = 11:00 ET = 20:30 IST** |
| Prizes | 1st $2,500 (+$300 Featherless) · 2nd $1,500 · 3rd $1,000 · **2 × $500 social** |
| Team | FluffyMargins (solo), invite-only, UTC+5:30 |
| Repo | https://github.com/aryangorde6/contour (public) |

**Hard requirements:** autonomous AI agent · Alpaca Trading API · MCP **or** CLI ·
**options in every strategy** · brand-new paper account at **$100,000** · submit the
account ID · one-page write-up (AI logic / risk gates / Alpaca infra).

**Judged on:** P&L · Technology Implementation · Creativity & Originality ·
Presentation & Execution · Social engagement. Roughly equal weight.

**Trading window:** Mon Aug 31 → Fri Sep 4, 11:00 ET. Five full sessions
(Labor Day 2026 is Sep 7, *after* the contest — many sources return 2025 data
where it fell on Sep 1; do not build a holiday gate for Sep 1).

---

## 2. Accounts and identity

| | |
|---|---|
| **Judged account** | `PA35XVXLIO0E` — in `ACCOUNT_ID.txt`, submit this to lablab |
| **Dev account** | `PA35MRNGUR91` — all prototyping and live round-trip tests |
| GitHub | `aryangorde6` (hackathon account; `aryangorde8` is the default) |
| Commits | **Never add a `Co-Authored-By` trailer.** Contributors must read `aryangorde6` alone. |

Both accounts are options level 3, $100,000.00, created 2026-08-30.
Repo-local `user.name`/`user.email` are set to aryangorde6; global is untouched.

Secrets set on the repo: `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`,
`ALPACA_DEV_API_KEY`, `ALPACA_DEV_SECRET_KEY`.
**Not set:** `FEATHERLESS_API_KEY` -- claim at
`featherless.ai/join/feather_request_pricing/ALPACA26` (code auto-applied,
$25, first-come-first-served), then key from profile -> API Keys, `fw-...`.

`ANTHROPIC_API_KEY` is **dead**: there is no card available and the Console
grants no trial credits to this org. Do not re-propose buying credits. The
hackathon's only free inference is Featherless, whose $25 covers the ~116
calls this week needs many times over.

### The brain: Bedrock GLM-5, on AWS credits

**There is no card, and every paid path needs one. Do not re-litigate this:**

| Path | Outcome | Evidence |
|---|---|---|
| Anthropic first-party | dead | Console grants this org no trial credit; only "Buy credits" |
| Featherless `ALPACA26` | dead | $25 promo applies, amount due $0, but Stripe still demands a card for future renewals. Cash App Pay is US-only |
| Bedrock, **Anthropic models** | dead | 403 `INVALID_PAYMENT_INSTRUMENT`. Anthropic on Bedrock is an AWS **Marketplace subscription**, which needs a card. Credits are not a payment instrument |
| **Bedrock, everything else** | **WORKS** | 93 models callable. Non-Anthropic models bill as ordinary AWS usage, so credits cover them |
| Google AI Studio | works, unused | Free tier, no card. Kept as fallback |

**The lesson: the payment wall was Anthropic-specific, not Bedrock-wide.** I
concluded "Bedrock is dead" from a sample that was entirely Anthropic models.
`ops/probe_bedrock.py` enumerates entitlements properly -- catalogue visibility
is not entitlement, so it sends a real Converse request to one id per family.

**Model chosen by bake-off, not reputation.** Six candidates held all three
schemas. The tiebreak was the blackout job on three known dates:

| Day (truth) | GLM-5 | Nova Pro / Llama-4 |
|---|---|---|
| Mon 8/31, nothing scheduled | 0 windows | **invented an ISM blackout** |
| Tue 9/1, ISM+JOLTS 10:00 | 09:30-10:20 | ok |
| Wed 9/2, ADP + Beige Book | both, right times | Beige Book ok, ADP time wrong |

Nova and Llama lifted Tuesday's ISM out of the regime brief and applied it to
Monday, which would stand the agent down on the one clear day of the week.
`zai.glm-5` on `us-east-1`, ~30s per call against a 15-minute cycle.

`contour/llm.py` is a provider seam: `AnthropicProvider`, `BedrockProvider` and
`OpenAICompatProvider` (Featherless + Gemini) behind one
`parse(system, user, schema)` contract, so the vendor is a config value rather
than an architecture. `CONTOUR_LLM` (`off`/`anthropic`/`bedrock`/`featherless`/
`gemini`) forces one; `CONTOUR_LLM_MODEL` overrides the model id.

Bedrock speaks the **Converse API**, not per-provider `invoke` schemas: one
body shape across all 93 models, so switching model is a string change.

Repo variables: `CONTOUR_LLM=bedrock`, `AWS_REGION=us-east-1`. Secret:
`AWS_BEARER_TOKEN_BEDROCK`. **Watch the token's expiry** -- Bedrock bearer
tokens can be short-lived, and a configured brain that 401s fails CLOSED, which
would veto every entry for the rest of the week while looking healthy. If that
happens, clear the secret or set `CONTOUR_LLM=gemini`; degraded still trades.

Verify any provider without trading: `python -m contour --brain-check`.

Featherless serves open weights over an OpenAI-compatible endpoint at
`https://api.featherless.ai/v1`, model `zai-org/GLM-5.2`, and does **not**
guarantee strict `json_schema`. So `OpenAICompatProvider` degrades in three
stages -- strict schema, then JSON mode with the schema inlined in the prompt,
then a re-ask carrying the validation error back -- and raises if all three
fail, which the existing fail-closed policy already handles. HTTP 403 means a
**gated model**: click "Unlock Model" on its page to accept the licence. That
is a human action; the code raises rather than retrying.

Integrating it is also worth points. The rules state partner tech must be
*integrated* to be eligible for partner prizes, and only one of ~22 visible
submissions lists Featherless.

### Competitors are closer than assumed

Read from the submissions list on 2026-08-31. "LLM proposes, deterministic
gates dispose, hash-chained audit trail" is **table stakes**, not our
differentiator:

- **Horizon Blackline** -- "the LLM proposes, deterministic risk gates
  authorize, every decision is hash-chained and auditable"
- **VRP Engine** -- "harvests the variance risk premium with defined-risk
  option spreads, risk gates, and Alpaca API + MCP + CLI"
- **AEGIS-Q** -- "bounded AI selects a pre-validated bullish or bearish
  spread -- or abstains -- while deterministic code controls contracts,
  position sizing, maximum loss"
- **EdgeStack** -- "journals every trade and every refusal"

So `WRITEUP.md` must lead with **skew-driven structure selection**
(`select.py`): the 25-delta skew z-score picks *which* of four structures to
sell. Everyone else gates a fixed structure; we choose among four. No visible
competitor claims this.

---

## 3. What Contour is

An autonomous options agent that measures the shape of the volatility surface
every 15 minutes and lets the measurement pick the structure.

```
vrp_ratio < 1.30   -> NO_TRADE     implied not rich enough
skew_z >= +0.8     -> PUT_CS       puts rich, sell puts only
skew_z <= -0.8     -> CALL_CS      calls rich, sell calls only
otherwise          -> CONDOR       both sides fairly priced
```

**The thesis:** everyone sells iron condors, and a condor sells both wings
unconditionally — so half the time you are selling the underpriced side.
Contour measures 25-delta skew first and sells only the rich side. The choice
is visible in the order history, not just the README.

**Universe: SPY, QQQ, IWM only.** This reverses all three candidate designs
(which wanted 48–66 single names) and the reasoning is decisive: Alpaca paper
fills a limit order only when marketable, so you cross the full spread on every
leg, twice. Single-name weeklies cost **$40–80 round trip against a $30–42
modeled edge** — friction exceeds the entire edge. The ETFs cost $8–20. Also:
Alpaca serves **no earnings-date endpoint on any plan**, so every single-name
design hung its most important gate on data that does not exist.

**Expiry locked to 2026-09-11.** Nothing can expire, be assigned, or
auto-exercise inside the judged window. No 0DTE — those never return Greeks.

**Honest P&L:** median +0.3% to +0.9%, ~65% chance positive, designed floor
−4%, realistic worst case −6% to −7%. **P(>+15%) is under 1%.** Defined-risk
premium selling *cannot* reach the +15–25% a top-3 P&L rank needs — the gain is
capped at the credit. Whoever posts the winning P&L number will have won a coin
flip. Optimize the four criteria that are not luck.

---

## 4. Hard-won facts — do not re-derive these

Each of these cost real time or would have broken the run.

| Fact | Consequence |
|---|---|
| **MCP cannot place multi-leg orders** ([alpaca-mcp-server#97](https://github.com/alpacahq/alpaca-mcp-server/issues/97), open since 2026-07-01) — the `legs` array arrives as a JSON string and fails pydantic validation | Execution routes through the **Alpaca CLI**, which handles it. Verified live: 4-leg condor → `status: accepted`. Read through MCP, write through CLI. |
| **`ALPACA_API_KEY` in the environment silently overrides an explicit `-p profile` flag** | Observed live: `alpaca account get -p dev` returned the *judged* account. `execute.py` never uses profiles; it passes credentials per subprocess and asserts `account_number` before every order. |
| **Greeks and IV are served free** (10,616 of 13,160 strikes) | No local Black-Scholes needed. |
| **0DTE contracts never return Greeks or IV** (Black-Scholes divides by time-to-expiry) | G6 vetoes on null and never coerces to zero. Structurally excludes 0DTE. |
| **Option quotes stop at 15:59:59.998 ET; trades run to 16:14:58 ET** | The free indicative quote feed sits behind the tape. G5 measures `quote_age_s` every cycle. Threshold is **20 min, not 15** — a 15-min veto would reject everything if the lag is systematic. |
| **Free plan = "indicative" options feed** (Alpaca staff: OPRA "randomized a bit"), while paper **fills match real NBBO** | Decision prices and fill prices come from different universes. Decided to **skip** the $99 Algo Trader Plus; instead we log fill-vs-decision-mid per leg and report it honestly. |
| **`client_order_id` must be unique per ladder rung** — Alpaca 422s on reuse | Rungs are `-r1/-r2/-r3`. A ladder reusing one id is rejected on rung two, guaranteed. |
| **No stop/stop_limit orders on multi-leg positions** | There is no resting protective order. The exit is a polling loop. `manage.py` was written *before* go-live for this reason. |
| **Closing spreads can 403 with code `40310000`** ("uncovered option contracts") | Fall back to legging out, **shorts bought back first**. Reversing that leaves a naked short between fills. |
| **Paper issues random ~10% partial fills** | Reconcile from actual `filled_qty` and per-leg fill prices. Trusting the request puts condor legs out of ratio → accidentally naked. |
| **NFP is Fri 2026-09-04 08:30 ET**, 60 min before the open, and Alpaca has **no extended-hours options** | You physically cannot react to it. Flat by Thursday's close; Friday is `VERIFY_ONLY`. |
| **The market prices a ~60% chance of a 25bp HIKE** (Warsh, post-Jackson Hole). Weak data is currently **bullish** — a −23K payroll print on 2026-08-07 produced a record S&P close | Hard-coded into `mind.py`'s prompt. A model on its training prior inverts this. |
| Snapshots carry greeks/IV/quotes but **no `open_interest`**; that lives on the Trading API contract object with `tradable` and `close_price` | `data.py` merges both. G5 screens on the Trading API fields. |
| `GET /v2/options/contracts` defaults to contracts expiring before the coming weekend | Always pass explicit expiration filters, and strike-band to ±12% or the chain call will 429. |

### Three calibration bugs found by writing tests/dry-runs first

1. **G9 credit floor.** A flat 20%-of-wing floor is arithmetically incompatible
   with 13-delta shorts (20% needs ~30-delta). It rejected *every* structure in
   the spec's own table. Now structure-aware: **8% verticals, 13% condors**,
   calibrated against a live measurement ($0.870 on a $5 wing = 17.4%).
2. **G5 liquidity.** A flat "spread ≤ 8% of mid" rejects every long wing — a
   6-delta SPY wing at $0.10/$0.14 is a normal 4-cent market but 33% of mid.
   Now percentage **or** absolute allowance, plus a package round-trip friction
   guard that enforces the ETF-only argument instead of assuming it.
3. **Skew priors.** The specified priors (SPY 4.5 / QQQ 4.0 / IWM 5.5) were ~2
   vol points too high because they were calibrated against the **13-delta** IV
   pair rather than the **25-delta** pair `skew25` is defined over. Every name
   read `z ≤ −0.9` → the agent would have sold CALL spreads on all three
   underlyings all week, a directional bet the map never intended. Reseeded from
   live measurement: **SPY 2.52 / QQQ 2.81 / IWM 3.10**.

---

## 5. The twelve risk gates

Pure functions, zero I/O, fixed order, evaluated before every order. The reason
is journaled whether the gate passes or fails. This section is the required
write-up's risk content.

| # | Gate |
|---|---|
| G1 | No entries below $97,000 NAV; full halt below $96,000 |
| G2 | No entries once session P&L is worse than −1.5% NAV |
| G3 | Book risk ≤ 2% Mon / 5% Tue / 8% Wed–Thu; ≤ 1.0% NAV per position |
| G4 | Max 6 concurrent, 2 per underlying, 1 new per underlying per cycle |
| G5 | OI ≥ 500, tradable, close_price present, spread within pct **or** $0.10, quote < 20 min stale, round-trip friction ≤ 30% of credit |
| G6 | delta and IV non-null on **all** legs — a missing Greek is a hard veto, never zero |
| G7 | Short \|delta\| ∈ [0.10, 0.16]; wings ∈ [0.04, 0.10]; condor net \|delta\| ≤ 0.08 |
| G8 | Expiry must equal 2026-09-11 exactly |
| G9 | Credit ≥ 8% (vertical) / 13% (condor) of wing width, checked at rung 3 |
| G10 | No entries inside an LLM-parsed or hard-coded event blackout |
| G11 | 10:05–15:15 ET; no entries after Thu 11:00; flatten Thu 15:45; Fri `VERIFY_ONLY` |
| G12 | Committed `HALT` file stops trading; deterministic unique `client_order_id` |

**Kill switch:** commit a file named `HALT` to `main`. Honored at the top of the
next cycle *and recorded in the journal*, so a judge can see it was respected.

---

## 6. Build status

**84 tests passing.** All core modules complete.

| File | Purpose |
|---|---|
| `contour/config.py` | Every threshold. Single source of truth. Deviations documented inline. |
| `contour/models.py` | Leg, Candidate, Book, Context, Measurement, Blackout |
| `contour/gates.py` | G1–G12, pure, zero I/O |
| `contour/journal.py` | Append-only SHA-256 hash chain, tamper-detecting |
| `contour/surface.py` | atm_iv, rv10, **vrp_ratio (a RATIO, not a difference)**, skew25, skew_z |
| `contour/select.py` | The four-branch structure map — the differentiator |
| `contour/structures.py` | Strike selection, wings by strike distance, sizing, **signed limit price** |
| `contour/execute.py` | CLI broker, account assertion, 3-rung ladder, fill reconciliation |
| `contour/manage.py` | Exits: TP 50% / stop 2.0× / breach 0.30×wing / Thu flatten; shorts-first legout |
| `contour/data.py` | DataSource seam (snapshots + contracts merged); replay swaps in here |
| `contour/clock.py` | Session phase; cron never trusts its firing time |
| `contour/mind.py` | Claude: blackout windows, regime multiplier, structure veto |
| `contour/loop.py` | One idempotent cycle; exits before entries, always |
| `contour/__main__.py` | `--once --dry --as-of --dev --verify` |

**`mind.py` design rule:** the LLM's outputs can only make the agent trade
**less**. Multiplier is `min(value, 1.0)` and scales the NAV used for *sizing*,
so `NONE` yields zero contracts. It cannot pick a strike, size, price, or
reverse a structure. Enforced structurally — `execute.py` does not import it.

**Two-tier failure policy:** not configured → degraded but **still trading**
(half size, hard-coded blackouts, no veto). Configured but failing → **fail
closed**.

### CI / cron

- `agent.yml` — schedule + `workflow_dispatch` **only**. Never `pull_request`:
  this is a public repo holding live trading credentials.
- `pages.yml` — publishes `dashboard/` to GitHub Pages on push to `main`.
  **No secrets**, no `pull_request` trigger, and it guards against
  root-relative URLs, which would work on Vercel's root but 404 under Pages'
  `/contour/` project path.
- `ci.yml` — runs on PRs, **no secrets**. Tests + grep-block on
  `close-all`/`cancel-all` + chain verify + import-without-credentials.
- Cron (UTC): `20 13 * * 1-5` pre-open · `*/15 14-19 * * 1-5` cycle ·
  `50 19 * * 4` Thursday flatten escalation.
- Concurrency group, `cancel-in-progress: false` — two writers would fork the
  hash chain.
- Journal and state publish to the orphan **`agent-state`** branch.
- **Scheduled runs are LIVE on the judged account** — `workflow_dispatch` inputs
  are empty on a schedule trigger, so `ARGS` is just `--once`.

---

## 7. What is left

1. ~~`WRITEUP.md`~~ — **done.** Split in two on 2026-08-31: `WRITEUP.md` is the
   brief's one-pager (idea, AI logic, twelve gates, Alpaca infra) and
   `TECHNICAL.md` holds everything else (bake-off table, calibration bugs,
   Bedrock payment wall, the honest P&L claim). Both name Bedrock + GLM-5.
   **Edit both when a fact changes** -- the gate list and the structure map
   appear in each.
2. ~~Dashboard~~ — **built and LIVE at
   https://aryangorde6.github.io/contour/** (HTTPS, chain verifies in-browser).
   Pages was enabled out of band with
   `gh api -X POST repos/aryangorde6/contour/pages -f build_type=workflow`,
   because GITHUB_TOKEN cannot create the site itself.
   **Still open: the Vercel side** — import at vercel.com/new (accept what
   `vercel.json` supplies), then add `contour.aryangorde.com` under
   Settings -> Domains.
   **Verify HTTPS specifically**, not just that the page loads: `crypto.subtle`
   exists only in a secure context, so before the certificate lands the chain
   badge silently degrades to an error while the rest of the page looks fine.
   Hosting is deliberately *not* on AWS despite the credits: S3 needs
   CloudFront + ACM to get HTTPS at all, and putting the dashboard on the same
   account as the brain couples the two failures that must not correlate
   during judging.
3. ~~`--replay`~~ — **done.** `contour/replay.py`: `Recorder` tees the
   `DataSource` seam into a fixture, `Replay` serves it back. Forces dry + a
   degraded brain so it is deterministic, and writes to `replay_out/` so a
   rehearsal can never land in the published audit trail. CI runs it with no
   secrets. **Re-record mid-session** for a better demo -- the committed
   fixture is pre-open, so its quotes are weekend-stale and G5 vetoes.
4. **Video** — MP4, **3:45–4:30** (under 3 min is explicitly scored "2 —
   Limited"), plus slides needing market analysis, revenue model, roadmap and
   competitive analysis (four slides most teams skip).
5. **Social** — 5 posts on X/LinkedIn tagging `@lablabai` and `@AlpacaHQ` **in
   the body**. Only 18 total likes across all 23 submissions; two $500 prizes
   are nearly uncontested. Reddit links likely do not qualify.
6. Claim Featherless `ALPACA26` and paste the key -- `mind.py` runs degraded
   at half size until it lands.

**Schedule:** hard code freeze Wednesday. Thu/Fri are packaging and
verification only. File a draft submission Wednesday night.

### The dashboard

One static file. No build, no backend, no framework. It fetches the agent's own
published state from `agent-state` over raw.githubusercontent (CORS is open,
`max-age=300`, so every request carries a cache-buster) and draws:

- **the structure map** — the three names on skew-z against VRP over the four
  decision zones. This is the differentiator made visible in one chart.
- surface, every gate result, the equity curve, the journal feed
- **the hash chain, recomputed client-side with WebCrypto.**

The chain check does **not** re-serialise the payload. `to_line` and
`_canonical` use the same `sort_keys`/`separators`, so the payload's canonical
bytes are already sitting inside the line — the JS slices them out by
brace-matching and hashes those. That verifies the file *as written* rather
than our idea of it, and sidesteps the fact that Python writes `0.0` where
`JSON.stringify` writes `0`. `tests/test_dashboard.py` runs the shipped
JavaScript under Node against a Python-written chain (including a tampered
record) and asserts identical verdicts.

Two bugs the tests caught, both mine, both in code that looked right:
`\\"` closed the string early in the brace matcher (the escape flag was read
one character late), and stripping `<`/`>` for XSS turned
`vrp 1.24 < 1.30` into `vrp 1.24 1.30` — escape, never censor.

`state.point()` appends the NAV series the curve is drawn from; it is
deliberately tolerant of a corrupt file, because a cosmetic curve is never
worth failing a trading cycle over.

**Journals are gitignored on `main` now.** A dev dry-run `journal/2026-08-31.jsonl`
had been committed by an incidental `git add -A`, and the runner restores
`journal/` from `agent-state` with a path-scoped checkout that does not delete
untracked-elsewhere files — so the live cron would have appended judged records
onto twelve dev dry-run records and published them as one chain. Journals are
agent output; they belong on `agent-state` only, exactly like `state/`.

---

## 8. Resume commands

```bash
.venv/bin/python -m pytest -q                                  # 84 tests
.venv/bin/python -m contour --dry --dev --as-of 2026-08-31T11:00
.venv/bin/python -m contour --verify                           # hash chain
.venv/bin/python verify_setup.py                               # judged account
gh run list --workflow="contour agent" --limit 5
gh workflow run "contour agent" -f dry=true -f dev=true        # safe rehearsal
```

The Alpaca CLI is at `~/go/bin/alpaca` (v0.0.14). Python venv is pinned to
**3.12** — the system 3.14 breaks `alpaca-py`.

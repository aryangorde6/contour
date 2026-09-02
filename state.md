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

**Judged on — four criteria, not five:** P&L performance · Technology
Implementation · Creativity & Originality · Presentation & Execution.
**Social engagement is a separate pair of awards, NOT a judged criterion**
(`alpaca-overview.md`, from the hackathon page). This matters for how effort is
allocated: the five social posts buy a different prize and contribute *nothing*
to 1st place. It also means P&L is **25%** of the main score rather than 20%,
on a criterion this design cannot win -- see the honest P&L note in §3.

**Field size:** ~3,300 participants / ~1,000 teams registered. The "23 visible
submissions" read on 2026-08-31 is an early snapshot, not the final field.

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
grants no trial credits to this org. Do not re-propose buying credits.

**The provider was deleted from `llm.py` on 1 Sep 2026.** It was unreachable
(no key, and last in the preference order), it was the only importer of the
`anthropic` dependency, and its secret was being piped into the job that holds
the live broker keys. The evidence table below is kept so the finding is not
rediscovered the hard way, not because the code still exists.

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

`contour/llm.py` is a provider seam: `BedrockProvider` and
`OpenAICompatProvider` (Featherless + Gemini) behind one
`parse(system, user, schema)` contract, so the vendor is a config value rather
than an architecture. `CONTOUR_LLM` (`off`/`bedrock`/`featherless`/`gemini`)
forces one; `CONTOUR_LLM_MODEL` overrides the model id. An unrecognised name
degrades rather than quietly answering as a different vendor.

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

**Honest P&L, options book:** median +0.3% to +0.9%, ~65% chance positive,
designed floor −4%. Defined-risk premium selling *cannot* reach the +15–25% a
top-3 P&L rank needs — the gain is capped at the credit.

**Plus the sleeve, added 2026-09-01 at the operator's explicit direction.** One
long QQQ position, $30,000 ceiling, 1.2% of NAV at risk behind a 4% stop. QQQ's
measured 20-day vol is 17.4% → 3-day sigma 1.90% → about ±$559 at one sigma on
$29,398. It roughly **triples the width of the distribution in both
directions**. This is a variance decision, not an edge decision, and it was
taken knowing that: the arithmetic said the best system in the strategy folders
returns +0.36% over the remaining window, so edge was never the available
lever. It is capped, stopped, gated S1–S7 and funded *out of* G3's ramp rather
than beside it. Do not describe the submission as pure defined-risk anywhere —
`TECHNICAL.md` states the difference explicitly and the deck must match.

**Plus a long-call tail, added 2026-09-01, also at the operator's explicit
direction and over a documented recommendation against it.** 11 QQQ Sep-11 720
calls at $4.03 = $4,433. Three facts to keep straight, because every judged
document now states them and they must not drift:

1. **It is negative EV.** The strike implies 15.2% vol against rv10 of 12.07%
   — a 1.23x premium, about −0.32% of NAV expected. It *pays* the VRP the
   options book harvests. This is stated in WRITEUP, TECHNICAL, the deck and
   `config.ACKNOWLEDGED_SYMBOLS`; do not soften it anywhere.
2. **It fixes a real flaw.** The condor is short the SPY 781 call, so the joint
   book paid *less* at +3σ (+0.11%) than at +1σ (+0.22%) — short its own
   upside. This half is genuine and independent of the outcome.
3. **It sat OUTSIDE the capital floor — that is now repaired.** The QQQ tail
   was closed 2026-09-01 for −$1,320, and the TQQQ tail that followed is
   carved OUT of G3's ceiling via `TAIL_RISK_BUDGET_PCT` rather than added
   beside it. Book 1.678% + sleeve 1.200% + tail 1.122% = exactly the 4.0%
   halt distance, asserted as an equality. The decoration bug is gone from
   the live config, not merely acknowledged. The floor was held by closing
   the entry ramp to 0 from 2026-09-02, not by loosening the cap that
   measures it. Loss stays bounded by premium and cannot gap through a stop;
   G1 still blocks entries below −3%/−4% but does NOT flatten.

**Correction to an earlier belief in this file's lineage: G1 does NOT flatten.**
`g1_capital_floor` is an *entry gate*; `should_exit` has four rules (clock,
profit target, credit-multiple stop, breach) and **none reads NAV**. There is
no NAV-triggered liquidation anywhere. Do not describe the −4% halt as a
stop-out of open positions — it stops new ones.

---

## 4. Hard-won facts — do not re-derive these

Each of these cost real time or would have broken the run.

| Fact | Consequence |
|---|---|
| **MCP cannot place multi-leg orders** ([alpaca-mcp-server#97](https://github.com/alpacahq/alpaca-mcp-server/issues/97), open since 2026-07-01) — the `legs` array arrives as a JSON string and fails pydantic validation | Execution routes through the **Alpaca CLI**, which handles it. Verified live: 4-leg condor → `status: accepted`. **Reads are `alpaca-py`, NOT MCP** — the docs claimed otherwise until 2026-08-31; there is no MCP client in the package. The contest's "MCP server and/or CLI" is satisfied by the CLI write path alone. |
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
| G3 | Book risk ramps by date to 1.678% (ceiling = G1 halt − sleeve − tail), 0 from 2026-09-02; ≤ 1.25% NAV per position |
| G4 | Max 6 concurrent, 1 per underlying (derived), 1 new per underlying per cycle |
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

**272 tests passing.** All core modules complete.

| File | Purpose |
|---|---|
| `contour/config.py` | Every threshold. Single source of truth. Deviations documented inline. |
| `contour/models.py` | Leg, Candidate, Book, Context, Measurement, Blackout |
| `contour/gates.py` | G1–G12, pure, zero I/O |
| `contour/journal.py` | Append-only SHA-256 hash chain, tamper-detecting |
| `contour/surface.py` | atm_iv, rv10, **vrp_ratio (a RATIO, not a difference)**, skew25, skew_z |
| `contour/select.py` | The four-branch structure map — the differentiator |
| `contour/structures.py` | Strike selection, wings by strike distance, sizing, **signed limit price** |
| `contour/execute.py` | CLI broker, account assertion, 3-rung ladder, fill reconciliation; **cancels a partial rung's residual** before the caller writes the book |
| `contour/manage.py` | Exits: TP 50% / stop 2.0× / breach 0.30×wing / Thu flatten; shorts-first legout |
| `contour/data.py` | DataSource seam (snapshots + contracts merged); replay swaps in here |
| `contour/positions.py` | **The open book, persisted across cron runs.** Without it every exit rule is dead code |
| `contour/replay.py` | `Recorder` tees the DataSource seam into a fixture; `Replay` serves it back with no credentials |
| `contour/llm.py` | Provider seam: Bedrock / Featherless / Gemini behind one `parse()` |
| `contour/state.py` | The dashboard snapshot + the equity series + `written_at.json` per-file timestamps + `next_cycle()` |
| `contour/clock.py` | Session phase; cron never trusts its firing time; `is_preopen()` names the 09:20 ET planning window |
| `contour/mind.py` | The brain: blackout windows, stand-down multiplier (not sizing), structure veto |
| `contour/loop.py` | One idempotent cycle; exits before entries, always |
| `contour/__main__.py` | `--once --dry --as-of --dev --verify --brain-check --record --replay` |

**`mind.py` design rule:** the LLM's outputs can only make the agent trade
**less**. It cannot pick a strike, size, price, or reverse a structure.
Enforced structurally — `execute.py` does not import it.

### 2026-08-31: the model was anchoring, so sizing moved out of it

`multiplier` came back **exactly 0.5 on sixteen consecutive cycles** with
mutually contradictory prose attached — *"implied is roughly double realized,
which normally favors short premium"*, then *"vol premium is NOT being paid"*,
then *"the vol premium is extremely rich"* — same market, same session. The
prose was generated; the number was not. And 0.5 is also the documented
degraded default, so nothing in the journal would ever have flagged it as
wrong. It was visible only because the multiplier is journaled every cycle.

`contour/regime.py` sizes now, from work researched, backtested and frozen
before this hackathon existed (`~/TRADINGVIEW_INDICATOR`, `~/fable_project`):

| System | Rule used | Evidence |
|---|---|---|
| Stage-2 | above a **rising** 30-week SMA — the persistent state, not the breakout entry | Weinstein; 30.5y / 738 round-trips / two universes. **PF 6.43 and 5.12 in PERCENT space**, excl top-3 4.67 / 4.01, CI 4.28–9.66 / 3.51–7.55. **Do not cite 3.53 / 3.77** — the source withdrew its rupee figures (per-name equity reset makes the pooled currency column size-weighted) |
| Ribbon | EMA 20>50>200, price above the 200 | 15 names / 10 sectors, 11 of 15 PF>1 |
| LRS-VT2 | `min(1, σ_LR/σ_20)` × two-speed ladder × overextension trim | Gayed & Bilello 2016 (Dow Award); Moreira & Muir 2017 (JF) |

**LRS supplies the magnitude** (the only one carrying a sizing formula); the
other two **confirm** — both standing takes the weight whole, one halves it,
neither is zero. Bounded at 1.0, so "can only trade less" still holds. Every
gate runs against the result; G3 caps book and per-position risk regardless.

**Blast radius is bounded by gates that already existed.** At weight 1.0 a
condor sizes to ~$1,230 max loss against G3's $1,250 per-position cap. G4's
per-name cap is now **derived** — `int(BOOK_RISK_CEILING_PCT /
MAX_POSITION_RISK_PCT)` — so it cannot hand G3 a book G3 must refuse. Since the
sleeve took its 1.2% carve-out and the tail its 1.122%, that is 1.678 / 1.25
→ **1 per name**, down from 3: funding both costs the options book two slots,
and that is the trade. No reachable book, sleeve and tail included, can reach
max loss through the capital floor; `tests/test_gates.py` and
`tests/test_sleeve.py` both assert it, a further test asserts the three
budgets sum to the halt distance *exactly* rather than merely fitting, and
another asserts that zeroing either carve-out hands its room straight back.

**Why these moved on 2026-09-01.** The old 1.0% × 2-per-name made G3's ramp
unreachable: 6% with all three names qualifying, 2% with one — and one is the
live case, because QQQ (1.13) and IWM (1.21) sat under the 1.30 VRP floor all
week. The book deployed 0.84% of a 4% allowance while the top rung of the
ramp was arithmetically untouchable. A rung no configuration can reach is not
a risk control. The VRP floor and the skew bands were **not** touched — those
are the thesis, and loosening them to book more premium is the one change
that would make the whole write-up dishonest.

**Live first reading: SPY 1.0, QQQ 1.0, IWM 0.5.** The ladder drops IWM to its
warning rung (below its 50d SMA), independently agreeing with the surface,
which reads IWM weakest. Two unrelated measurements flagging the same name is
worth more than either alone.

**Two transfers, stated in `regime.py`, `WRITEUP.md` and `TECHNICAL.md` rather
than buried:** (1) Stage-2 and the ribbon were validated on Indian equities and
are used here on US ETFs — Stage-2 is Weinstein, US literature returning home,
and the ribbon is generic trend-following, but it is still a transfer; (2) all
three are *long-equity* systems sizing a *short-premium* book, justified
because both are short volatility and die in the same regime, which is exactly
what the vol-scaling term measures.

**Rejected, and why — do not revisit:**

| Candidate | Why not |
|---|---|
| TQQQ VP+TPO (`src/tqqq_vp_tpo_*.pine`) | The only US intraday work and the only 4-day-compatible holding period, but **unvalidated by its own header**. Running an unbacktested system on the judged account three days out contradicts the whole submission |
| NIFTY 5m/15m family (10 files) | `docs/FINDINGS.md` is a terminal negative result: PF 0.63–0.80 across 5 entries × 3 exits × 2 timeframes. Knowingly trading a system the research killed |
| Stage-2 as an **entry** system | Median winner held 9–10 months, <1 trade per name per year. Over four days it produces nothing. Only its regime *state* is usable |
| Trend-aware structure selection | **Right, and deferred on purpose.** The map breaks ties toward the condor, which sells calls into a confirmed uptrend — the exact error the thesis was written against. But it changes `select.py`, the differentiator every judged document describes, and a put spread collects $0.33 against the condor's $0.81, so it would *cost* P&L this week. It is the roadmap slide and one line in the video instead |

### `~/TRADINGVIEW_INDICATOR/fallacy-auditor` — and the citation it caught

A grounded reasoning auditor with three surfaces, two of which need no model
and were run against this project's sources on 2026-08-31:

```bash
PYTHONPATH=~/TRADINGVIEW_INDICATOR/fallacy-auditor/src \
  .venv/bin/python -m fallacy_auditor <file.pine|trades.csv|reasoning.txt>
```

- **Pine linter** (deterministic): all three strategies the sizer is built on
  — `LRS_VT.pine`, `stock_stage2_trend_weekly.pine`,
  `stock_swing_ribbon_pullback.pine` — return **clean: no mechanical red
  flags**. No `lookahead_on`, no `calc_on_every_tick`, no wall-clock in logic.
  The signals were validated for mechanical bias before being adopted.
- **Profit-factor auditor** (deterministic): this is what caught the citation
  error. It fires two fragility warnings on the Stage-2 OOS universe *in rupee
  space* — PF 3.77 → 1.57 when 3 of 355 trades are removed, second half worth
  13% of the first — which is exactly what I had cited as clean supporting
  evidence.
- **Reasoning audit** (LLM): needs Ollama, **which is not installed on this
  machine**. `ollama pull qwen2.5:7b` (~4.7 GB) would enable running it over
  `WRITEUP.md` and `TECHNICAL.md`. Not done; noted as available.

**The correction, and why it makes the evidence stronger.** The source
project's own correction pass (`docs/STAGE2_BACKTEST_RECON.md` §0) **withdrew
every rupee-denominated statistic**: `replicate_stage2.py:73` resets equity
inside the per-name loop, so 39 names each compound an independent account and
pooling the currency column size-weights trades by *when* they happened. The
replacement is percent space, and it is better: **PF 6.43 / 5.12** (vs the
withdrawn 3.53 / 3.77), surviving top-3 removal at **4.67 / 4.01**, top-3
worth 27.4% / 21.8% of gross gain rather than 58.3%. Caveats that survive:
the median trade returns +0.3% / −1.0% — the edge is entirely in the tail —
and U2 decays 44% first half to second.

**Note for anyone reading the source:** the Pine header of
`stock_stage2_trend_weekly.pine` still quotes the withdrawn rupee figures.
That is their repo's stale claim, not ours; we cite percent.

**The lesson worth keeping, and it is the same one as the anchored
multiplier:** a number that looks like supporting evidence deserves the same
scrutiny as a number that looks wrong. Both errors this week were figures
nobody re-derived — 0.5 because it matched the degraded default, 3.53 because
it was in a header. Cite the artifact, not the summary.

### `Replay.newest` served the wrong fixture for two days

It sorted by **filename**. `2026-08-31-1305et.json` sorts before
`2026-08-31-preopen.json`, so `--replay` — the demo whose whole job is showing
twelve passing gates — served a fixture recorded four hours earlier and showed
a **G5 staleness veto** instead. Now sorted on the fixture's own
`captured_utc`, with an unreadable file never winning. The claim in README and
WRITEUP was true when written and had silently stopped being true.

**Two-tier failure policy:** not configured → degraded but **still trading**
(half size, hard-coded blackouts, no veto). Configured but failing → **fail
closed**. A fail-closed cycle journals an explicit `STAND_DOWN` per name; it
used to surface as "could not assemble a valid structure from the chain",
which read as a market-data problem rather than a brain outage.

### CI / cron

- `agent.yml` — schedule + `workflow_dispatch` **only**. Never `pull_request`:
  this is a public repo holding live trading credentials.
- `pages.yml` — publishes `dashboard/` to GitHub Pages on push to `main`.
  **No secrets**, no `pull_request` trigger, and it guards against
  root-relative URLs, which would work on Vercel's root but 404 under Pages'
  `/contour/` project path.
- `fixture.yml` — records a live replay fixture and commits it to `main`.
  Schedule + dispatch only, **dev account only**, `--dry`, and it verifies the
  recording replays with non-stale quotes before committing. A `GITHUB_TOKEN`
  push does not trigger other workflows, which is why it verifies in-job.
- `ci.yml` — runs on PRs, **no secrets**. Tests + grep-block on
  `close-all`/`cancel-all` + chain verify + import-without-credentials.
- Cron (UTC): `20 13 * * 1-5` pre-open · `*/15 14-19 * * 1-5` cycle ·
  `50 19 * * 4` Thursday flatten escalation · `20,50 14 * * 2` fixture.
- Concurrency group, `cancel-in-progress: false` — two writers would fork the
  hash chain. A long cycle therefore queues the next rather than racing it.
- **`timeout-minutes: 25`**, not 12. Three entry ladders at 3 rungs x 90s is
  810s before any LLM call. A killed job skips the publish step, and since the
  open book lives in `state/`, losing that publish loses a freshly-recorded
  position — it recreates the unmanaged-book bug.
- Verify and publish both run **`if: always()`**, with publish gated on
  `steps.verify.outcome == 'success'`. A cycle that crashes after a fill still
  has a position and a journal worth keeping; a corrupt chain is still never
  pushed.
- The publish step builds its commit with a **scratch `GIT_INDEX_FILE` +
  `write-tree` + `commit-tree`**, never a branch checkout. It used to stash
  `journal`/`state` and check out `agent-state`, but `stash --include-untracked`
  skips *ignored* files and both directories are gitignored, so the outputs
  stayed in the tree and the checkout aborted. It also refuses to push an empty
  tree: a publish that wipes the audit trail is worse than one that fails.
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
   secrets. **Re-recorded mid-session** on 2026-08-31 at 13:05 ET:
   `fixtures/2026-08-31-1305et.json` puts a real SPY condor through **all
   twelve gates, every one passing** (credit $0.79, rung-3 $0.70 vs a $0.65
   floor), with QQQ and IWM refused on VRP. The pre-open fixture is kept --
   it is the record of day one, and it is where G5's staleness veto is
   visible -- but `--replay` picks the newest, which is the good one.
   `.github/workflows/fixture.yml` re-records on Tuesday at 10:20 ET so this
   does not go stale again; it uses the throwaway account, runs dry, refuses
   to commit a fixture whose quotes are already stale, and stops recording on
   its own once `BOOK_RISK_RAMP` runs out.
4. **Video** — MP4, **3:45–4:30** (under 3 min is explicitly scored "2 —
   Limited"), plus slides needing market analysis, revenue model, roadmap and
   competitive analysis (four slides most teams skip). **Script and all seven
   slides are written: `ops/video.md`.** Eight timed beats at ~600 words for
   4:05–4:15, the tab order to set up first, and what to cut if it runs long
   (never §5 — the twelve green gates are the strongest evidence we have).
   Live numbers in it are marked ⚠️ and must be re-read off the dashboard at
   record time. **The deck is built too: `dashboard/deck.html`**, seven slides
   published alongside the dashboard at `/contour/deck.html`, a 1280x720 stage
   scaled to the window so it reads the same at 1080p and on a laptop. Its
   market-analysis slide fetches the agent's own published VRP rather than
   quoting a stale number, falling back to the last known values so it never
   renders an em-dash on stage. **What remains is recording it, which needs
   the operator.**
5. **Social** — 5 posts on X/LinkedIn tagging `@lablabai` and `@AlpacaHQ` **in
   the body**. Only 18 total likes across all 23 submissions; two $500 prizes
   are nearly uncontested. Reddit links likely do not qualify. **All five are
   written: `ops/social.md`** — one per day Mon→Fri, each a different idea,
   every X version verified under 280 characters, with a longer LinkedIn
   variant and the image to attach. If only two go out, post the MCP finding
   and the bug post-mortem: those are the two that engage non-judges.
   **Posting is the operator's — nothing is published on anyone's behalf.**
6. ~~Featherless `ALPACA26`~~ — **moot.** The brain runs on Bedrock GLM-5 with
   AWS credits; Featherless stays wired as a fallback but needs a card.
7. ~~**Audit findings not yet fixed**~~ — **all six done, 2026-08-31.** See
   "The six audit findings, and what each one turned into" below.

8. **The lablab submission itself** — **nothing is filed yet, and that is the
   largest single risk on the project.** Everything else is finished and none
   of it scores until a submission exists. `ops/submission.md` holds every
   form field written out: tagline, short description, the full description in
   judging order, the tech tags (and which ones *not* to claim -- MCP is a
   documented blocker, not a dependency), the fixed facts table, and a
   morning-of pre-flight. **File a draft immediately with a placeholder video
   link** -- lablab allows edits until the deadline, so the only unrecoverable
   failure this week is the form not being open at 11:00 ET Friday.

**Schedule:** hard code freeze Wednesday. Thu/Fri are packaging and
verification only. File a draft submission Wednesday night.

### Live state as of 2026-08-31 close

**Two SPY iron condors are open on the judged account**, both expiring
2026-09-11, tracked in `state/positions.json` on `agent-state`:

| order | filled ET | credit | max loss/ct | strikes |
|---|---|---|---|---|
| `1b003d45` | 10:39 | $0.80 | $420 | 745/740 P · 781/786 C |
| `b4507a9a` | 10:09 | $0.82 | $418 | 745/740 P · 781/786 C |

NAV ~$99,990. They exit-check every cycle now; the profit target fires at
~$0.40 and the stop at ~$1.60. **They will be flattened by the scheduled
Thursday 15:45 ET flatten** -- which is only true because of the fix below.

Nothing more opened today after 11:00 ET: the regime call set a `no_new_entries
_after` cutoff of 11:00 ET, and G11 enforced it for the rest of the session.

### The dashboard

One static file. No build, no backend, no framework. It fetches the agent's own
published state from `agent-state` over raw.githubusercontent (CORS is open)
and draws:

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

**Do not trust the `?t=` cache-buster.** Measured 2026-08-31: raw.githubusercontent sends `max-age=300` and does not key its CDN on the query string --
the busted and bare URLs returned the same stale object from one edge while
curl from another edge already had the new one. The page can therefore sit up
to five minutes behind. That is fine against a 15-minute cycle, and the fix is
not a cleverer buster: every panel states the age of what it shows.

`state.point()` appends the NAV series the curve is drawn from; it is
deliberately tolerant of a corrupt file, because a cosmetic curve is never
worth failing a trading cycle over.

**Journals are gitignored on `main` now.** A dev dry-run `journal/2026-08-31.jsonl`
had been committed by an incidental `git add -A`, and the runner restores
`journal/` from `agent-state` with a path-scoped checkout that does not delete
untracked-elsewhere files — so the live cron would have appended judged records
onto twelve dev dry-run records and published them as one chain. Journals are
agent output; they belong on `agent-state` only, exactly like `state/`.

### Every CI failure so far, and why — do not re-investigate

| Run | Cause | Fixed by |
|---|---|---|
| `ci` 33329914945 | `Multiple top-level packages discovered: ['contour', 'journal']` | `c3677e7` — declare the package explicitly |
| `pages` 33375780059 | Pages not enabled on the repo | enabled out of band via the API |
| `pages` 33375832427 | `GITHUB_TOKEN` cannot *create* a Pages site, even with `pages: write` — "Resource not accessible by integration". `actions/configure-pages`' own `enablement: true` cannot get around it either | flag removed; site created once with `gh api -X POST .../pages -f build_type=workflow` |
| `contour agent` 33376951937 | `stash --include-untracked` skips ignored files → `checkout -B agent-state` aborted on `state/heartbeat.json` | scratch-index publish, above |

The standing annotation is a **Node 20 deprecation warning** on
`actions/checkout@v4` and `actions/setup-python@v5`. GitHub already force-runs
them on Node 24, so it is cosmetic. **Do not bump action pins on the
credentialed trading workflow during the contest** — wrong risk for a warning.

### The audit that found all of this

Five parallel reviewers (live path · CI/CD · code landed that day · LLM layer ·
truthfulness of the judged docs), each finding capped at 4, then per-finding
adversarial verifiers prompted to **refute by default**. 9 confirmed, 1
refuted, 10 reported unverified under a per-dimension cap.

Two process notes worth keeping:

1. **An empty result is not a clean bill of health.** The first run returned
   "no defects survived verification" because all five agents had died on a
   usage limit before running. The journal held five `started` records and zero
   results. Always check the journal before believing a negative.
2. **Verification earned its keep.** The one refuted finding ("CI verifies zero
   journal files") was factually right about the files but wrong about the
   consequence — the step above it already covers the case.

### The six audit findings, and what each one turned into

All six were fixed on 2026-08-31, after the book bug. None was on the money
path, but four of them were the same bug class as the book bug: **something
was computed and nobody read it, or defaulted and nobody passed it.**

| Finding | Now | Pinned by |
|---|---|---|
| a partially-filled rung left its residual working at the broker, and `legs_balanced` was computed but never read | the rung cancels, **re-reads** (the cancel races the book) and records the post-cancel fill; unequal legs journal an `unbalanced_fill` alarm, and `loop.py` stops opening anything else that cycle | `tests/test_execute.py` |
| the dashboard's "Structures opened" counted gate-passing candidates | counts `position_opened` records; the subtitle splits refusals into gate-vetoed / LLM-vetoed / unfilled | `tests/test_dashboard.py`, running the shipped JS |
| `cycle_count` was always 0 | `state.next_cycle()` counts from the last published heartbeat, because nothing else survives a container | `tests/test_loop.py` |
| the regime call was handed `{}` — the model that sizes the whole book never saw the surface | measured VRP per underlying, off a per-cycle chain cache, and journaled alongside the multiplier | `tests/test_loop.py` |
| the 13:20 UTC pre-open cron could not reach the blackout code | `clock.is_preopen()`; the CLOSED branch plans the day, journals a `plan` record and publishes `state/plan.json`, which the dashboard renders above the decisions | `tests/test_loop.py` |
| `--replay` printed only failing gate reasons, though README and WRITEUP promise every one | every reason prints, marked `[ok  ]` / `[VETO]`; one `_passed()` helper, matching the dashboard's `/^G\d+ ok/` | `tests/test_replay.py` |

Two things worth carrying forward:

- The regime fix came with a **per-cycle measurement cache**. The exit check,
  the regime call and the entry loop all ask for the same chain; three round
  trips can return three different answers inside one cycle, and then the book
  is sized against numbers it never published.
- `tests/test_loop.py` exists for exactly one bug class: **a defaulted argument
  nobody passes is invisible to every test that calls the callee directly.**
  It asserts on what the cycle *hands* its collaborators, and it pins the
  `main()` call site by source, because `main()` cannot be reached without live
  credentials.

### 2026-08-31: the open book was never passed to the cycle

Found by a five-dimension adversarial audit, **after it had already fired.**
Two SPY condors filled at 10:09 and 10:39 ET and no later cycle could see them.

`run_cycle(open_positions=())` -- nothing ever passed it, and nothing ever
constructed a `ManagedPosition`. `submit_with_ladder`'s fill record was
journaled and dropped. Consequences, all confirmed in code:

- profit target, stop, breach and the **Thursday flatten** all iterated an
  empty tuple, so no exit rule could ever run
- `Book(positions=())` reported zero open risk; G4 printed "0 open, 0 in SPY"
  while two condors sat on the judged account. Only the LLM's 11:00 ET cutoff
  stopped it re-opening the same condor every 15 minutes all week
- `TECHNICAL.md`'s "flat before the deadline by design" was false

**Fixes, all live:**

| Was | Now |
|---|---|
| no persistence | `contour/positions.py` under `state.ROOT`, restored and published by the existing workflow steps |
| credit from the mid | credit from per-leg fill prices -- the stop is 2.0x credit, so an inflated credit takes more loss than designed |
| requested qty recorded | actual `filled_qty` recorded; a partial leaves fewer contracts than the candidate |
| mark from entry-time legs (frozen at the entry credit forever, so TP/stop could never fire) | re-priced from the live chain each cycle |
| `spot = 0.0` on a failed measurement -- reads as far below every short put, a phantom BREACH | holds; only the clock rule runs unpriced |
| exit `base_id` constant per position | 15-min bucketed, or the first failed close poisons every later one incl. the flatten |
| 12-min job timeout vs a cycle that can take longer; a kill skips publish and loses the fill | 25 min; verify+publish run `if: always()`, gated on the chain verifying |
| 90s LLM timeout x2 retries x5 calls = 15 min | 45s (measured ~30s) |
| fail-closed brain journaled as "could not assemble a valid structure from the chain" | explicit `STAND_DOWN` per name |
| one bad time string failed the whole blackout call closed | that window is dropped and named; a broken brain still fails closed |
| docs claimed reads go "through MCP" -- they never did | corrected: reads are `alpaca-py`, writes are the CLI |

`ops/repair_book.py` rebuilt the book from the orders Alpaca actually filled
and it was published to `agent-state`. Verified live: `[book] 2 tracked
position(s), 4 option leg(s) at the broker`, both exit-checked each cycle with
marks that move.

**The lesson worth keeping: a default argument nobody passes is invisible.**
`open_positions: Sequence[ManagedPosition] = ()` looked like a tested, working
feature -- `manage.py` was fully unit-tested -- because the tests called
`should_exit` directly and never asked whether anything reached it. There was
no `tests/test_loop.py`. Unit tests on a function prove nothing about whether
it is wired in.

---

## 8. Resume commands

```bash
.venv/bin/python -m pytest -q                                  # 171 tests
.venv/bin/python -m contour --replay                           # no credentials needed
.venv/bin/python -m contour --brain-check                      # is the LLM alive?
.venv/bin/python -m contour --dry --dev --as-of 2026-08-31T11:00
.venv/bin/python -m contour --verify                           # hash chain
.venv/bin/python verify_setup.py                               # judged account
.venv/bin/python ops/repair_book.py                            # rebuild the book, dry
gh run list --workflow="contour agent" --limit 5
gh workflow run "contour agent" -f dry=true -f dev=true        # safe rehearsal
```

Read the live book without cloning anything:

```bash
curl -s https://raw.githubusercontent.com/aryangorde6/contour/agent-state/state/positions.json
```

The Alpaca CLI is at `~/go/bin/alpaca` (v0.0.14). Python venv is pinned to
**3.12** — the system 3.14 breaks `alpaca-py`.

## 2026-09-01 ~18:00 UTC — the tail is closed, and why

Sold 11 QQQ260911C00720000 at **$2.83** for **$3,113** against $4,433 paid.
Realised **−$1,320**, which was the entire account drawdown: everything else in
the book was +$11 combined at the time.

The reason is measured rather than nervous. `research/strategy_backtest.py`
drives the agent's own `choose_structure` / `assemble` / `build` / `should_exit`
over 387 cycles of real option prices (Jan 2024 – Aug 2026) and finds **+0.93%
in total, t = +0.37** — no edge, and unstable besides: 2024 −$2,546 (PF 0.53),
2025 +$3,475 (PF 2.38), 2026 flat to the dollar. Holding the tail meant paying
15.1% implied against 13.1% realised — ~15% over fair value — to buy variance
that nothing else in the book justified.

The same backtest turned OFF the volume-profile filter shipped hours earlier
(`PROFILE_ENABLED = False`): it cut P&L +$926 → +$230 while avoiding **no**
losses. The touch-rate finding behind it (z = +6.20) is real and reproduces;
touch probability was simply the wrong objective. That is the day's actual
lesson and it is written into `config.py` beside the flag.

Book now: 21 QQQ shares (stop resting) + the SPY Sep-11 condor. NAV $98,692
(−1.31%). `ACKNOWLEDGED_SYMBOLS` is back to empty.

**Open, and now the largest thing by far: the lablab submission and the video.**
Neither is filed. P&L is the first thing judged, but an unfiled entry scores
zero regardless of it.

## 2026-09-01 ~18:25 UTC — a second tail, and the ramp closed to pay for it

Bought **6 TQQQ Sep-11 70C at $1.87 ($1,122)** on an explicit instruction. The
evidence against it was put on the record first and is worth keeping: across
five years, a TQQQ gap down does **not** predict a bounce (next-day mean +0.19%
at gaps ≤ −3%, t = +0.42; **negative** at ≤ −4%, which is where today's −3.96%
sits), and simulating the call purchase at *realised* vol with *zero* spread
still returns −3.39% on premium at the matching gap size. Live, the calls cost
~1.4× realised vol on the agent's own rv10 measure.

It breaks no gate — inside the $1,250 per-position cap, and carved out of the
book ceiling via `TAIL_RISK_BUDGET_PCT` exactly as the sleeve is.

**What it did break, and how that was fixed.** Three things surfaced:

1. `_held_symbols` matched `("SPY2","QQQ2","IWM2")`, so `TQQQ2…` was invisible
   to the book check. Added the prefix; the leg is now acknowledged, not an
   orphan, and `_orphan_legs` returns empty against the live broker.
2. G3's ceiling is derived from `START_NAV`, but the halt is a fixed NAV
   *level*, and $1,408 of realised loss had already eaten the distance to it.
   Committed risk $2,549 against $2,592 of room left **$43**.
3. The opening ramp rung was hard-coded at 0.02 and inverted the ramp once the
   ceiling shrank — Monday looser than Wednesday. Now a fraction of the ceiling.

The ramp is **0.00 from 2026-09-02**. Sizing constants were deliberately left
alone: bending a per-position cap to accommodate a drawdown is how a capital
floor stops meaning anything. Exits are unaffected — the exit loop runs ahead
of the gates, so targets, stops, breaches and the scheduled flatten still fire
and every cycle still journals.

A `HALT` file was tried first and reverted: `HALT_FILE` is cwd-relative, so it
switched off 27 tests as well as the agent. The ramp is the honest place.

NAV $98,592 (−1.41%). Book: 21 QQQ + SPY condor + 6 TQQQ 70C. 253 tests pass.

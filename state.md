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
**Not set:** `ANTHROPIC_API_KEY`, `FEATHERLESS_API_KEY` (both empty in `.env`).

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

**47 tests passing.** All core modules complete.

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

1. **`WRITEUP.md`** — required one-pager. §5 above is the risk content verbatim.
2. **Dashboard + hosted demo URL** — scored on whether the link *works*. Use
   Vercel, **not** Streamlit Community Cloud (sleeps after 12h idle; judges look
   days later). Reads `agent-state` via raw.githubusercontent.
3. **`--replay`** against a recorded fixture — a judge with no Alpaca keys must
   be able to run the repo.
4. **Video** — MP4, **3:45–4:30** (under 3 min is explicitly scored "2 —
   Limited"), plus slides needing market analysis, revenue model, roadmap and
   competitive analysis (four slides most teams skip).
5. **Social** — 5 posts on X/LinkedIn tagging `@lablabai` and `@AlpacaHQ` **in
   the body**. Only 18 total likes across all 23 submissions; two $500 prizes
   are nearly uncontested. Reddit links likely do not qualify.
6. Optional: `ANTHROPIC_API_KEY` (activates `mind.py`), Featherless (the $300
   partner add-on).

**Schedule:** hard code freeze Wednesday. Thu/Fri are packaging and
verification only. File a draft submission Wednesday night.

---

## 8. Resume commands

```bash
.venv/bin/python -m pytest -q                                  # 47 tests
.venv/bin/python -m contour --dry --dev --as-of 2026-08-31T11:00
.venv/bin/python -m contour --verify                           # hash chain
.venv/bin/python verify_setup.py                               # judged account
gh run list --workflow="contour agent" --limit 5
gh workflow run "contour agent" -f dry=true -f dev=true        # safe rehearsal
```

The Alpaca CLI is at `~/go/bin/alpaca` (v0.0.14). Python venv is pinned to
**3.12** — the system 3.14 breaks `alpaca-py`.

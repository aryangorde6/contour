# Contour — an options agent that lets the measurement pick the structure

**Alpaca AI Trading Agents Hackathon · Options Alpha Agents**
Paper account `PA35XVXLIO0E` · [github.com/aryangorde6/contour](https://github.com/aryangorde6/contour)
Live dashboard: **[aryangorde6.github.io/contour](https://aryangorde6.github.io/contour/)**

---

## The idea in four lines

Everyone sells iron condors. A condor sells **both** wings unconditionally,
which means half the time you are selling the underpriced side and calling it
diversification.

Contour measures the shape of the volatility surface first, and sells only the
side that is actually rich:

```
vrp_ratio < 1.30   ->  NO_TRADE             implied is not rich enough to sell
skew_z    >= +0.8  ->  PUT_CREDIT_SPREAD    puts rich -- sell puts, not the cheap calls
skew_z    <= -0.8  ->  CALL_CREDIT_SPREAD   calls rich -- sell calls, not the cheap puts
otherwise          ->  IRON_CONDOR          both sides fair -- sell both
```

`vrp_ratio` is ATM implied over 10-day realized volatility: *are we being paid
a premium at all?* `skew_z` is the 25-delta put/call IV difference, scored
against a per-underlying prior: *which side is the premium concentrated on?*

The consequence is visible in the order history rather than only the README.
Across a week the same account shows put spreads, call spreads, condors, and
flat sessions — because the surface changed, not because a model changed its
mind. Universe is SPY, QQQ and IWM on a single locked expiry (2026-09-11):
three liquid names, one expiry, so every comparison is like-for-like.

---

## AI logic

An LLM sits in the loop, and **every one of its wired outputs can only make the
agent trade less.** This is enforced structurally, not by convention:
`execute.py` never imports `mind.py`, so no model output can reach an order.

| The model may | The model may never |
|---|---|
| Name event windows to stand down in | Choose a strike |
| Return a size multiplier, clamped at 1.0 | Size a position |
| Veto a proposed structure | Price an order |
| | Reverse or widen anything |

Strikes, sizing and pricing are arithmetic, and language models should not do
arithmetic that money depends on. What is genuinely language-shaped — *"which
of today's scheduled releases should a short-premium book stand down for?"* —
is what it is asked.

**Failure policy is two-tiered, deliberately asymmetric:**

- **No brain configured** → run *degraded*: half size, on a hard-coded event
  table, no veto. An agent that stops trading because a language model is
  absent is not autonomous, it is dependent.
- **Brain configured but answering off-schema** → **fail closed**: veto, size
  zero. A configured brain returning garbage is a real signal, not a hiccup.

`llm.py` is a **provider seam**: Amazon Bedrock, Featherless, Google AI Studio
and Anthropic sit behind one `parse(system, user, schema)` contract, so the
vendor is a config value rather than an architecture — `CONTOUR_LLM` picks one
and `CONTOUR_LLM_MODEL` overrides the model id. The judged run is **GLM-5 on
Bedrock**, reached through the **Converse API**: Nova, Qwen, Mistral, Llama and
GLM each take a different `invoke` body, while Converse takes the same one for
all of them, so changing model is a string rather than a new code path.

**The model was chosen by bake-off, not by reputation.** Six candidates all
returned schema-valid output for all three jobs, so schema compliance separated
nothing. The tiebreak was blackout accuracy on three dates whose calendars we
already knew:

| Prompted for (truth) | GLM-5 | Nova Pro / Llama-4 |
|---|---|---|
| Mon 8/31 — nothing scheduled | 0 windows | **invented an ISM blackout** |
| Tue 9/1 — ISM + JOLTS, 10:00 ET | 09:30–10:20 | correct |
| Wed 9/2 — ADP + Beige Book | both, correct times | Beige Book only; ADP time wrong |

Nova and Llama lifted Tuesday's ISM out of the regime brief and applied it to
Monday, which would have stood the agent down on the one clear session of the
week. In a design where the model can only subtract, a hallucinated blackout is
not a cosmetic error — it is the *only* kind of error it can still make.

Bedrock's Converse API does not offer structured outputs, so the schema is
inlined in the system prompt, the reply is brace-matched out (these models fence
their JSON and think out loud in front of it), validated against the pydantic
model, and on a mismatch re-asked **once** with the validation error handed
back. Two failures raise, which the fail-closed policy above already covers.
Reasoning models return their chain in a separate content block, so only text
blocks are read. The OpenAI-compatible providers get a similar three-stage
ladder, since open-weight endpoints do not guarantee strict `json_schema`
either.

One finding worth recording, because it cost a day: **the Bedrock payment wall
is Anthropic-specific.** Anthropic models on Bedrock are AWS *Marketplace*
subscriptions and 403 with `INVALID_PAYMENT_INSTRUMENT` on a credit-funded
account; every other model bills as ordinary AWS usage and works. Concluding
"Bedrock is unavailable" from an all-Anthropic sample was wrong —
`ops/probe_bedrock.py` sends a real Converse request to one model per family,
because catalogue visibility is not entitlement, and found **93 callable
models**.

The journal records **which brain answered**, so any result can be reproduced
against the model that produced it.

---

## Risk gates

Twelve pure functions. Zero I/O, fixed order, short-circuiting, evaluated
before every order. **The reason is journaled whether the gate passes or
fails**, so a no-trade cycle is exactly as auditable as a trade.

| # | Gate |
|---|---|
| G1 | No entries below $97,000 NAV; full halt below $96,000 |
| G2 | No entries once session P&L is worse than −1.5% NAV |
| G3 | Book risk ≤ 2% Mon / 5% Tue / 8% Wed–Thu; ≤ 1.0% NAV per position |
| G4 | Max 6 concurrent, 2 per underlying, 1 new per underlying per cycle |
| G5 | OI ≥ 500, tradable, `close_price` present, spread within pct **or** $0.10, quote < 20 min stale, round-trip friction ≤ 30% of credit |
| G6 | delta and IV non-null on **all** legs — a missing Greek is a hard veto, never coerced to zero |
| G7 | Short \|delta\| ∈ [0.10, 0.16]; wings ∈ [0.04, 0.10]; condor net \|delta\| ≤ 0.08 |
| G8 | Expiry must equal 2026-09-11 exactly |
| G9 | Credit ≥ 8% (vertical) / 13% (condor) of wing width, checked at the worst ladder rung |
| G10 | No entries inside an LLM-parsed or hard-coded event blackout |
| G11 | 10:05–15:15 ET; no entries after Thu 11:00; flatten Thu 15:45; Fri `VERIFY_ONLY` |
| G12 | Committed `HALT` file stops trading; deterministic unique `client_order_id` |

Three of these constants were **wrong on first contact with live quotes**, and
the tests caught each one:

- **G9** started as a flat 20%-of-wing credit floor. That is arithmetically
  incompatible with 13-delta shorts — 20% needs roughly a 30-delta — so it
  rejected every real structure. Recalibrated structure-aware against a
  measured $0.870-on-$5-wing quote.
- **G5** tested spread as a percentage of mid only. A 6-delta SPY wing at
  $0.10/$0.14 is a perfectly normal four-cent market and 33% of mid. Now
  percentage **or** absolute, plus a package round-trip friction guard.
- **Skew priors** were validated against the 13-delta IV pair rather than the
  25-delta pair `skew25` is actually defined over. All three names read
  z ≤ −0.9, meaning the agent would have sold call spreads on everything, all
  week, for a units reason. Reseeded from live measurement.

**Kill switch:** commit a file named `HALT` to `main`. It is honored at the top
of the next cycle *and written to the journal*, so the record shows it was
respected.

**Exits before entries, unconditionally**, because Alpaca holds no resting stop
on a multi-leg position — the management code was written before go-live, not
after. Legout buys back every **short** before selling any long, so the account
is never momentarily naked.

---

## Alpaca infrastructure

**Read through MCP, write through the CLI** — and that split is a finding, not
a preference. The MCP server cannot place multi-leg orders: the `legs` array
arrives as a JSON string and fails pydantic validation
([alpaca-mcp-server#97](https://github.com/alpacahq/alpaca-mcp-server/issues/97)).
The CLI places the identical order correctly; a live 4-leg SPY condor returned
`status: accepted, order_class: mleg`, then cancelled clean.

Other things the live environment taught us:

| Finding | Consequence in the code |
|---|---|
| `ALPACA_API_KEY` in the environment **silently overrides** an explicit `-p profile` flag | Credentials are passed per subprocess; profiles are never used |
| Wrong-account risk is unrecoverable | `assert_account()` refuses to trade unless the account number matches, before *every* order |
| Alpaca 422s on a reused `client_order_id` | Each ladder rung gets its own suffix (`-r1/-r2/-r3`) |
| Snapshots carry Greeks and quotes but **no** `open_interest` | Merged with the Trading API contract object for OI, `tradable`, `close_price` |
| 0DTE contracts never return Greeks | Expiry locked to 7DTE, enforced by G8 |
| Free-feed option quotes stop at 15:59:59 ET while trades run to 16:14 | Quote age instrumented at 20 min, and fill-vs-decision-mid logged |

**Execution** is a three-rung limit ladder from mid toward the bid, never a
market order on entry; `reconcile()` reads actual `filled_qty` and per-leg fill
prices rather than assuming the order worked.

**Autonomy** is GitHub Actions: a pre-open cycle parses event windows, then one
cycle every 15 minutes from 10:00–15:45 ET, then a scheduled Thursday flatten.
State and the journal publish to an orphan `agent-state` branch. The agent
workflow has **no `pull_request` trigger** and CI runs **with no secrets** —
the trust boundary is drawn so that a pull request from a stranger can never
reach a credential.

**The journal is a SHA-256 hash chain**, append-only, verified in CI. Every
decision, every refusal, every gate reason and every fill is in it, and the
chain proves the record was not edited after the fact.

**The dashboard re-verifies that chain in your browser.** It is one static file
with no backend: it fetches the raw journal from `agent-state`, walks the chain
with WebCrypto SHA-256 and prints the same verdict `python -m contour --verify`
prints, so the audit trail does not rest on our word for it. Its test suite runs
that same JavaScript under Node against a Python-written chain — including a
deliberately tampered record — and asserts both implementations reach an
identical verdict, because a badge that always reads "verified" is decoration
rather than evidence.

---

## What we claim, and what we don't

The strategy harvests the variance risk premium with defined risk. It is
designed to be *right often and wrong small*: most weeks it collects a modest
credit, and its bad outcome is bounded by the wing width on every position.
The realistic distribution over five sessions is a small positive return, not a
moonshot — `P(>+15%)` is under 1% by construction, because nothing here has
undefined risk.

The account is flat before the deadline by design: the Thursday flatten
converts an open mark-to-market into a settled number, so the P&L a judge sees
cannot drift while positions on a September 11 expiry sit unmanaged after
submission. It also avoids unwinding a short-premium book into Friday's payroll
print on a data feed we have measured going stale before the close.

Every claim above is checkable. The full suite runs with no credentials of
any kind -- `pip install -e '.[dev]' && pytest` -- and CI additionally
verifies the journal's hash chain and imports the package with all keys
unset, so a reviewer can confirm the trust boundary rather than take it on
faith.

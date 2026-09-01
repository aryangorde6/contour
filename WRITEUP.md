# Contour — an options agent that lets the measurement pick the structure

**Alpaca AI Trading Agents Hackathon · Options Alpha Agents**
Paper account `PA35XVXLIO0E` · [github.com/aryangorde6/contour](https://github.com/aryangorde6/contour)
Live dashboard: **[aryangorde6.github.io/contour](https://aryangorde6.github.io/contour/)**

## The idea

Everyone sells iron condors. A condor sells **both** wings unconditionally, so
half the time you are selling the underpriced side and calling it
diversification. Contour measures the surface first and sells only the rich
side:

```
vrp_ratio < 1.30   ->  NO_TRADE             implied is not rich enough to sell
skew_z    >= +0.8  ->  PUT_CREDIT_SPREAD    puts rich -- sell puts, not cheap calls
skew_z    <= -0.8  ->  CALL_CREDIT_SPREAD   calls rich -- sell calls, not cheap puts
otherwise          ->  IRON_CONDOR          both sides fair -- sell both
```

`vrp_ratio` is ATM implied over 10-day realized vol (*are we paid at all?*);
`skew_z` is the 25-delta put/call IV gap scored against a per-underlying prior
(*which side holds the premium?*). The consequence shows up in the order history,
not just the README: across a week the account shows put spreads, call spreads,
condors and flat sessions because the surface moved, not because a model changed
its mind. SPY, QQQ and IWM, one locked expiry (2026-09-11).

## AI logic

GLM-5 on **Amazon Bedrock**, through the Converse API. **Every wired output can
only make the agent trade less** — enforced structurally, not by convention:
`execute.py` never imports `mind.py`, so no model output can reach an order.

| The model may | The model may never |
|---|---|
| Name event windows to stand down in | Choose a strike |
| Veto a proposed structure | **Size** or price a position |
| Stand the whole book down | Reverse or widen anything |

Strikes, sizing and pricing are arithmetic, and language models should not do
arithmetic that money depends on. Only what is genuinely language-shaped —
*"which of today's releases should a short-premium book stand down for?"* — is
asked of it.

**Failure policy is deliberately asymmetric.** No brain configured → run
*degraded*: half size, hard-coded event table, no veto; an agent that stops
because a model is absent is not autonomous. Brain configured but answering
off-schema → **fail closed**: veto, size zero.

`llm.py` is a provider seam — Bedrock, Featherless, Google and Anthropic behind
one `parse(system, user, schema)` contract — so the vendor is a config value.
The model was picked by **bake-off, not reputation**: all six candidates
returned schema-valid output, so the tiebreak was blackout accuracy, where Nova
Pro and Llama-4 both invented a Monday ISM window that would have stood the
agent down on the week's one clear session.

## Risk gates

Twelve pure functions. Zero I/O, fixed order, evaluated before every order.
**The reason is journaled whether a gate passes or fails**, so a no-trade cycle
is exactly as auditable as a trade.

| # | Gate | # | Gate |
|---|---|---|---|
| G1 | NAV floor: no entries under $97k, halt under $96k | G7 | Short delta 0.10–0.16, wings 0.04–0.10, condor net ≤ 0.08 |
| G2 | Session P&L worse than −1.5% NAV stops entries | G8 | Expiry must equal 2026-09-11 exactly |
| G3 | Book risk ramp 2/5/8% by weekday; ≤ 1% NAV per position | G9 | Credit ≥ 8% vertical / 13% condor of wing, worst rung |
| G4 | ≤ 6 concurrent, 2 per name, 1 new per name per cycle | G10 | No entries inside an event blackout |
| G5 | OI ≥ 500, spread pct **or** $0.10, quote < 20 min, friction ≤ 30% | G11 | 10:05–15:15 ET; last entry Thu 11:00; flatten Thu 15:45 |
| G6 | Null delta or IV on any leg is a hard veto, never zero | G12 | Committed `HALT` stops trading; unique `client_order_id` |

Three of these constants were wrong on first contact with live quotes and the
tests caught each — most consequentially the skew priors, validated against the
13-delta IV pair rather than the 25-delta pair `skew25` is defined over, which
would have sold call spreads on everything all week for a units reason.
**Exits run before entries, unconditionally**, because Alpaca holds no resting
stop on a multi-leg position; legout buys back every short before selling any
long, so the account is never momentarily naked.

## Alpaca infrastructure

**Every order goes through the Alpaca CLI, and that is a finding, not a
preference.** We tried the MCP server first: it cannot place multi-leg orders,
because the `legs` array arrives as a JSON string and fails pydantic validation
([alpaca-mcp-server#97](https://github.com/alpacahq/alpaca-mcp-server/issues/97),
open since 2026-07-01). The CLI places the identical order correctly — a live
4-leg SPY condor returned `status: accepted, order_class: mleg`. Market reads
go through the official `alpaca-py` SDK (option chain snapshots merged with
Trading API contract objects, since snapshots carry Greeks but no
`open_interest`). Entries go out as a three-rung limit
ladder from mid toward the bid, never a market order, and `reconcile()` reads
actual `filled_qty` and per-leg fills — paper issues random partial fills, and
trusting the request puts condor legs out of ratio.

**Autonomy is GitHub Actions**: a pre-open cycle parses event windows, a cycle
every 15 minutes from 10:00–15:45 ET, and a scheduled Thursday flatten. The
agent workflow has **no `pull_request` trigger** and CI runs **with no
secrets**, so a pull request from a stranger can never reach a credential.

**The journal is an append-only SHA-256 hash chain**, verified in CI — every
decision, refusal, gate reason and fill. The dashboard re-verifies that chain
**in your browser** with WebCrypto and prints the same verdict
`python -m contour --verify` prints, so the audit trail does not rest on our
word for it.

---

Full detail — the bake-off table, every calibration bug, the Bedrock
payment-wall finding, and what we deliberately do *not* claim about P&L — is in
**[TECHNICAL.md](TECHNICAL.md)**. Nothing here needs our credentials to check:
`pytest` runs the whole suite, and `python -m contour --replay` puts a committed
fixture of real SPY/QQQ/IWM quotes through the same measurement, selection and
gate code the live agent runs, printing every gate reason. CI runs both with no
secrets on every push.

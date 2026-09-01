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

## The strike comes from the traded distribution too

The rule above picks *which side* to sell. A 0.13-delta band then picks *where*
— and delta is a modelled number: the probability of finishing in the money
under a lognormal centred on spot. A volume profile measures the other thing,
where price has actually traded, and the two disagree often enough to matter.

Five years of SPY/QQQ/IWM daily bars, distance held fixed in sigma units so the
strike is identical in both arms and only the value area moves. At the distance
the agent actually sells, an eight-day touch happens **32.8%** of the time when
the call strike sits inside the traded value area and **21.9%** when it sits
outside (z = +6.20). It holds in every volatility regime, all three names and
five of six years, and 2026 is the strongest year in the sample.

So Contour declines to sell upside into the band where price has been living.

Calls only — and that is a result, not a shortcut. The same test on puts comes
back with the *wrong sign* and no significance (−1.6 points, z = −1.08),
because downside gaps over the profile instead of grinding through it. Filtering
puts would be decoration.

It can only ever *remove* a strike. If every in-band call strike is inside the
value area, the call side is dropped and the condor is sized, gated and
journaled as a put spread — what the book holds, not what it first asked for.
An unreadable profile vetoes nothing.

On 2026-09-01 it bound on one of three names: SPY and QQQ were clear, while IWM
traded at 291 against a 298–303 value area, making its 0.13-delta 300 call a
strike sitting inside the zone. That condor became a put spread.

Both tables regenerate from live bars via `research/profile_edge.py`.

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

`llm.py` is a provider seam — Bedrock, Featherless and Google behind one
`parse(system, user, schema)` contract — so the vendor is a config value.
The model was picked by **bake-off, not reputation**: all six candidates
returned schema-valid output, so the tiebreak was blackout accuracy, where Nova
Pro and Llama-4 both invented a Monday ISM window that would have stood the
agent down on the week's one clear session.

## Risk gates

Twelve pure functions for the options book, seven more for the sleeve below.
Zero I/O, fixed order, evaluated before every order.
**The reason is journaled whether a gate passes or fails**, so a no-trade cycle
is exactly as auditable as a trade.

| # | Gate | # | Gate |
|---|---|---|---|
| G1 | NAV floor: no entries under $97k, halt under $96k | G7 | Short delta 0.10–0.16, wings 0.04–0.10, condor net ≤ 0.08 |
| G2 | Session P&L worse than −1.5% NAV stops entries | G8 | Expiry must equal 2026-09-11 exactly |
| G3 | Book risk ramp 2/2.8% by weekday, ceiling = G1's halt distance **minus the sleeve**; ≤ 1.25% NAV per position | G9 | Credit ≥ 8% vertical / 13% condor of wing, worst rung |
| G4 | ≤ 6 concurrent, 2 per name (derived from G3's ceiling), 1 new per name per cycle | G10 | No entries inside an event blackout |
| G5 | OI ≥ 500, spread pct **or** $0.10, quote < 20 min, friction ≤ 30% | G11 | 10:05–15:15 ET; last entry Thu 11:00; flatten Thu 15:45 |
| G6 | Null delta or IV on any leg is a hard veto, never zero | G12 | Committed `HALT` stops trading; unique `client_order_id` |

Three of these constants were wrong on first contact with live quotes and the
tests caught each — most consequentially the skew priors, validated against the
13-delta IV pair rather than the 25-delta pair `skew25` is defined over, which
would have sold call spreads on everything all week for a units reason.
**Exits run before entries, unconditionally**, because Alpaca holds no resting
stop on a multi-leg position; legout buys back every short before selling any
long, so the account is never momentarily naked.

## The directional sleeve, and what it costs

Defined-risk premium selling is capped at the credit it collects: the median
week is under one percent, and no amount of gate-tightening changes the shape
of that payoff. So the agent also runs **one long QQQ position, $30,000
ceiling** — stated plainly, it buys **variance, not edge**.

It is sized by the vol-scaling rule of **LRS-Fortress**, the best risk-adjusted
system in our own research set (28.0% CAGR, Sharpe 0.94, max drawdown −49.3%
over 55 years): notional is the ceiling times `lrs_weight`, the *same* function
that already sizes the options book — a position, not a second model. It
inherits Fortress's sizing and none of its 30% gold diversification, which is
where most of that drawdown improvement actually comes from.

It has **seven gates of its own, S1–S7**, sharing G1's capital floor, G2's daily
halt, G11's window and G12's kill switch by reading the same constants. S3 is
stricter than the options book: a directional bet needs **both** trend systems
standing, never one. Its 4% stop **rests GTC at the broker** — the one thing the
options book cannot do, because Alpaca serves no resting stop on a multi-leg
position.

**It is paid for out of the same capital floor, not added beside it.** Its 1.2%
budget is *subtracted* from G3's ramp, which falls 4.0% → 2.8% and costs the
options book its third position per name. Both books at simultaneous max loss
still sit exactly on G1's −4% halt, and a test asserts it. Set
`SLEEVE_NOTIONAL = 0` and the previous numbers return exactly. *The tail
position below sits outside this arithmetic, and says so.*

## The tail position, and the floor it does not fit behind

On 2026-09-01 the agent also bought **11 QQQ Sep-11 720 calls for $4,433**.
Three things about it need saying plainly.

**It is long premium.** It *pays* the variance risk premium the rest of this
document argues is worth harvesting — 15.2% implied against 12.07% realized, a
1.23× premium, about **−0.32% of NAV in expectation**. It is the only
negative-expectancy trade in the book, and it is not dressed up as anything
else.

**It repairs a real flaw.** The condor is short the SPY 781 call, so across the
joint book a +3σ rally paid *less* than +1σ (+0.11% against +0.22%) — the book
was short its own upside. Long calls convert that into positive convexity, and
that is a genuine structural improvement independent of how the bet lands.

**It does not fit behind the capital floor, and that is the honest cost.** The
two books at simultaneous max loss sit exactly on the −4% halt; this adds a
further **4.43%** of bounded loss on top, so total reachable loss is about
**8.4% behind a 4% halt**. That is precisely the arithmetic `config.py` names
as *decoration* — reintroduced knowingly, at the operator's direction, as a
variance decision under a contest whose payoff is convex in rank.

The mitigations are real but partial, and worth stating exactly. The loss is
**bounded by the premium paid** and cannot gap through a stop the way the
sleeve can — a long option is the one instrument here whose worst case is
known in advance and unconditional. G1 still blocks new entries below −3% and
−4%. What G1 does **not** do is flatten: it is an entry gate, and no exit rule
in `manage.py` reads NAV at all. Anyone auditing this account should know that
before inferring a protection that is not there.

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

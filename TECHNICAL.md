# Contour — engineering detail

The submission's one-page write-up is **[WRITEUP.md](WRITEUP.md)**. This is the
long version: the same design, plus the measurements, dead ends and calibration
bugs behind each decision.

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

### The universe is three names because we measured the alternative

By Tuesday only SPY cleared the 1.30 floor, which pins the book. The cheap
question to ask before loosening the floor is whether the premium is simply
somewhere else, so we swept sixteen optionable ETFs through the same
measurement (`ops/vrp_survey.py`, read-only, places nothing):

| clears 1.30 | VRP | 25d short premium | legs with IV |
|---|---|---|---|
| SPY | 1.49 | $2.54 | 269 |
| EFA | 1.38 | $0.35 | 56 |
| XLE | 1.38 | $0.43 | 45 |

and QQQ 1.17, IWM 1.27, DIA 1.19, XLF 1.14, USO 1.15, XLU 1.07, XLK 1.00,
TLT 0.95, SLV 0.95, EEM 0.89, SMH 0.83, GLD 0.71 below it. FXI carried no
25-delta pair inside the band at all.

The two names that clear the floor besides SPY are **untradeable at this
account size, not merely unattractive**. A 25-delta EFA put is worth $0.35
and an XLE put $0.43, so a defined-risk structure on either collects cents;
G5 caps round-trip friction at 30% of credit and a penny-wide spread on a
$0.35 leg is already 6% *per leg*, four legs each way. Sizing up to
compensate multiplies the friction with the credit — it does not dilute it.
So the floor stays where it is: the premium that exists elsewhere this week
is on instruments too thin to harvest, which is the same friction argument
that excluded single names, now measured across ETFs rather than asserted.

**And now measured on the single names too.** That exclusion had been reasoned
from quoted spreads rather than sampled, so on 2026-09-01 with the market open
it was measured across ten liquid Nasdaq large caps on the same Sep-11 expiry,
short strikes ~2.5% out and wings ~0.7% wider:

| | credit | round-trip friction | friction / credit |
|---|---:|---:|---:|
| NVDA | $1.39 | $0.54 | **39%** |
| AAPL | $1.40 | $0.72 | 51% |
| MSFT | $2.25 | $1.36 | 60% |
| NFLX | $0.51 | $0.34 | 66% |
| TSLA | $1.71 | $1.28 | 75% |
| META | $2.11 | $3.76 | 178% |
| AVGO | $2.84 | $6.64 | 234% |
| GOOGL | $0.57 | $1.40 | 246% |
| AMD | $1.80 | $4.42 | 246% |
| AMZN | $0.00 | $0.60 | collects nothing |

G5 caps friction at 30% of credit. The *best* name on the board is 39%, a
third above the limit, and the median is 75%. On META, AMD, GOOGL and AVGO the
round trip costs two to three times the entire credit -- you would lose money
on a trade that won. The universe is three ETFs because that is what the
spreads permit, not because the list was never revisited.

A second, quieter blocker: `skew_z` needs a per-underlying `SKEW_PRIOR`, and
these names have none. Adding one would mean guessing the mean and standard
deviation of a distribution we have not observed — which is exactly the
hard-coding the roadmap wants replaced by learned priors, not extended.

### A measured edge that did not survive a P&L test

**This filter is built, tested, documented -- and turned OFF.** It is kept as a
negative result because how it failed is more useful than the fact that it did.

The finding below is real: a call strike inside the traded value area is touched
far more often than one the same distance away outside it, at z = +6.20 over
five years. It reproduces. It survives every slice.

It also loses money. Across 387 cycles of real option prices through the
agent's own code (`research/strategy_backtest.py`, Jan 2024 - Aug 2026):

```
                        total P&L   profit factor   stops
delta strikes only          +$926            1.09      43
+ volume-profile filter     +$230            1.02      44
```

It avoided *no losses*. The stop count went UP by one; what fell was
profit-target income, $11,463 to $10,890. The reason is that touch probability
is the wrong objective. A condor's losses come from the put side in a selloff,
and the call credit forfeited to dodge a touch is collected 73% of the time.
Downgrading a CONDOR (PF 1.20) to a PUT_CS (PF 1.10) trades a better structure
for a worse one to solve a problem the book did not have.

The lesson is specific: **a statistically robust signal measured against the
wrong objective is still a losing feature.** The touch study was sound and the
conclusion drawn from it was not, because P&L was never tested until afterwards.

What follows is the original measurement, kept intact.

### The strike comes from the traded distribution, not only the modelled one

The structure map above chooses *which side* to sell. `SHORT_DELTA_BAND` then
chooses *where*: the short strike is the one nearest 0.13 delta. Delta is the
risk-neutral probability of finishing in the money under a lognormal centred on
spot — smooth, symmetric, memoryless. The tape is none of those things.

`contour/profile.py` adds the measured distribution beside the modelled one. A
volume profile over the last 20 sessions gives a POC (the modal traded price)
and a value area holding 70% of the volume. When a strike the delta band called
safe sits *inside* that band, the model and the tape disagree, and the tape is
describing where price has actually been spending its time.

**The measurement.** Five years of daily bars on SPY, QQQ and IWM (2021-09-03
to 2026-09-01, 1253 bars each), horizon 8 trading days to match the expiry the
agent trades. Distance is held **fixed in sigma units**, so the strike is
identical in both arms and the only thing that moves is where the value area
sits. At 1.13 sigma — the 0.13-delta strike this feeds — an eight-day touch
happens:

```
call strike INSIDE the value area    32.8%   (247 / 753)
call strike OUTSIDE the value area   21.9%   (627 / 2859)     z = +6.20
```

Ten points of touch probability, on strikes the delta band had already
accepted. It survives slicing: every vol regime (+6.8, +14.3, +20.4 points for
high/mid/low), all three names (SPY +15.9, QQQ +12.9, IWM +4.8), and five of
six calendar years. The exception is 2022, at +1.1 points and insignificant —
in a sustained downtrend call strikes are barely tested at all, so there is
nothing there to find. It is not decaying: 2026 is the strongest year in the
sample at +29.2.

**Only calls are filtered, and the reason is the null result.** The identical
test on the put side returns the *wrong sign* with no significance: −1.6 points
at 1.13 sigma, z = −1.08. The asymmetry is mechanical rather than mysterious.
Upside is a grind that walks up through the profile, so a call strike inside
the traded band is standing in the path. Downside gaps over the whole profile
in a session or two, so where the value area sits says nothing about it.
Applying the filter to puts would be decoration, and the roadmap's own standard
for decoration is that it gets deleted.

**What it can do, and what it cannot.** It can only *remove* a call strike from
consideration. It never adds one, widens a band, sizes anything, or reaches an
order. If it removes every in-band call strike, the call side is dropped and a
CONDOR is journaled and sized as a PUT_CS — the book must record what it holds,
not what the skew map first asked for. An unreadable window, an absent
`bars` seam, or fewer than 10 bars all produce a `degraded` profile that vetoes
nothing, because a profile we could not read is not evidence of a busy strike.

Live on 2026-09-01 it bound on exactly one name (with the flag on). SPY (spot 762, VAH 775, strike
778) and QQQ (spot 709, VAH 723, strike 730) were clear. IWM was trading at 291
against a value area of 298–303 — price sitting *below* its own recent range —
and the 0.13-delta call was 300, inside it. The condor became a put spread, and
the journal says so in the same record that carries the fill.

The claim is reproducible rather than asserted: `research/profile_edge.py` and
`research/profile_edge_robustness.py` print the tables above from live bars.

---

## AI logic

An LLM sits in the loop, and **every one of its wired outputs can only make the
agent trade less.** This is enforced structurally, not by convention:
`execute.py` never imports `mind.py`, so no model output can reach an order.

| The model may | The model may never |
|---|---|
| Name event windows to stand down in | Choose a strike |
| Veto a proposed structure | **Size a position** |
| Stand the whole book down | Price an order |
| | Reverse or widen anything |

Strikes, sizing and pricing are arithmetic, and language models should not do
arithmetic that money depends on. What is genuinely language-shaped — *"which
of today's scheduled releases should a short-premium book stand down for?"* —
is what it is asked.

### Sizing was on the left of that table until we measured it

On 2026-08-31 the regime call returned a size multiplier of **exactly 0.5 on
sixteen consecutive cycles**, with reasoning that contradicted itself across
calls — *"implied is roughly double realized, which normally favors short
premium"*, then *"vol premium is NOT being paid"*, then *"VIX at 2026 lows
with realized vol at 7.6% means the vol premium is extremely rich"* — same
market, same session. The prose was generated; the number was not. It was
anchoring, and because the multiplier scales the NAV used for sizing, half
the book was determined by an artifact that no journal entry would have
flagged, because 0.5 is also the documented degraded default.

It was only visible because the multiplier is journaled every cycle. The fix
is `contour/regime.py` — sizing from three trend systems that were researched,
backtested and frozen before this hackathon existed:

| System | Rule used | Evidence behind it |
|---|---|---|
| Stage-2 | price above a **rising** 30-week SMA (the persistent state, not the breakout entry) | Weinstein Stage Analysis; replicated in-house over 30.5 years / 738 round-trips / two universes — PF **6.43** and **5.12**, surviving top-3 removal at 4.67 / 4.01, bootstrap 95% CI 4.28–9.66 / 3.51–7.55 |
| Ribbon | EMA 20>50>200 and price above the 200 | validated long-only across 15 names in 10 sectors, 11 of 15 profitable |
| LRS-VT2 | `min(1, σ_longrun/σ_20d)` × two-speed ladder × overextension trim | Gayed & Bilello 2016 (Dow Award); Moreira & Muir 2017, *Journal of Finance* |

**The Stage-2 figures above are percent-space, and that matters.** The source
project's correction pass **withdrew** its rupee-denominated numbers (PF 3.53
and 3.77): its simulator resets equity inside the per-name loop, so each of 39
names compounds an independent account and pooling the currency column
size-weights trades by *when* they happened rather than measuring per-trade
edge. In rupee space the second universe fails a fragility check — PF collapses
3.77 → 1.57 when three of 355 trades are removed. In percent space, which is
size-invariant, it does not: 5.12 → 4.01, with the top three worth 21.8% of
gross gain rather than 58.3%. I cited the withdrawn figures in the first draft
of this section; they are corrected here, and the correction makes the evidence
*stronger*, not weaker. Two caveats that survive it: the median trade returns
+0.3% and −1.0%, so the edge is entirely in the tail, and the second universe's
profit factor decays 44% from first half to second.

**LRS-VT2 supplies the magnitude** — it is the only one of the three carrying
an explicit position-sizing formula. **Stage-2 and the ribbon confirm**: both
standing takes the weight whole, one halves it, neither means no trend support
and the weight is zero. The result is bounded at 1.0, so the property that
matters is unchanged — this layer can still only make the agent trade *less*.

**Two transfers, stated rather than buried.** Stage-2 and the ribbon were
validated on Indian equities and are applied here to US ETFs; Stage-2 is
Weinstein, US-origin literature returning home, and the ribbon is generic
trend-following, but it is still a transfer. And all three are *long-equity*
systems sizing a *short-premium* book — justified because leveraged long
equity and short option premium are the same trade in disguise, both short
volatility, both destroyed by the same regime, which is exactly what the
vol-scaling term measures.

First live reading: **SPY 1.0, QQQ 1.0, IWM 0.5** — the two-speed ladder drops
IWM to its warning rung, independently agreeing with the volatility surface,
which reads IWM as the weakest of the three.

**Failure policy is two-tiered, deliberately asymmetric:**

- **No brain configured** → run *degraded*: half size, on a hard-coded event
  table, no veto. An agent that stops trading because a language model is
  absent is not autonomous, it is dependent.
- **Brain configured but answering off-schema** → **fail closed**: veto, size
  zero. A configured brain returning garbage is a real signal, not a hiccup.

`llm.py` is a **provider seam**: Amazon Bedrock, Featherless and Google AI
Studio sit behind one `parse(system, user, schema)` contract, so the vendor is
a config value rather than an architecture — `CONTOUR_LLM` picks one
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
| G3 | Book risk ramps by date to a 1.678% ceiling (halt distance − sleeve − tail), closed to 0 from 2026-09-02; ≤ 1.25% NAV per position |
| G4 | Max 6 concurrent, 1 per underlying (derived from G3's ceiling), 1 new per underlying per cycle |
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

## The directional sleeve

**The problem it answers.** Everything above is capped at the credit it
collects. A defined-risk premium seller's median week is under one percent, and
tightening gates does not change the shape of that payoff. The sleeve is the
deliberate answer: **one long QQQ position, $30,000 ceiling, buying variance
rather than edge.** It is stated in those words in `contour/sleeve.py` and on
the dashboard, because dressing it up as anything else would be false.

**What sizes it.** `regime.lrs_weight` — the vol-scaling rule of LRS-Fortress
(Gayed & Bilello 2016 + Moreira & Muir 2017 + a 30% gold sleeve): 28.0% CAGR,
Sharpe 0.94, max drawdown −49.3% over 55 years, against 0.75 and −66.6% for
LRS-VT2 alone. That function already exists and already sizes the options book,
so the sleeve adds a *position*, not a second model. Notional is
`SLEEVE_NOTIONAL × lrs_weight`, which genuinely binds: a warning-rung entry
(above the 200d, below the 50d) deploys half, a vol-hot tape deploys less again,
below the 200d nothing opens.

**What is not transferred, said out loud.** Fortress is 70/30 equity/gold and
the gold leg earns most of that drawdown improvement. Only the equity leg runs
here, because the instrument was specified. Quoting the 0.94 Sharpe as though
this were the whole system would be false.

**Two deliberate deviations from the source system.** (1) *It does not
re-size.* Fortress trims continuously as `lrs_weight` decays; over a four-day
window each trim pays a spread to express a distinction the horizon cannot
resolve, so the halving rung is treated as an **exit** — strictly more
conservative than the source. (2) *It has a hard price stop the source does
not.* Fortress exits on trend, which on daily closes means an overnight gap is
worn in full.

### Seven more gates, S1–S7

| # | Gate |
|---|---|
| S1 | G1's capital floor, read from the same constants — one floor per account |
| S2 | G2's daily loss halt |
| S3 | **Both** Stage-2 and the ribbon standing, and `lrs_weight ≥ 0.5`. A degraded regime refuses: degraded is not bullish |
| S4 | One sleeve position; notional ≤ the $30,000 ceiling |
| S5 | Modeled stop loss ≤ the 1.2%-of-NAV carve-out G3's ramp was reduced by |
| S6 | G11's window; never opened after the Thursday flatten deadline |
| S7 | G12's `HALT` file and unique `client_order_id` |

S1, S2, S6 and S7 restate their G-equivalents rather than importing them,
because the options gates take a `Candidate` with legs, a wing width and an
expiry — none of which a share of QQQ has. The **thresholds** are shared, so
there is one capital floor and one kill switch, not two.

S3 is *stricter* than the options book on purpose. The composite sizer trades
at half size on a single confirmation; the sleeve does not trade at all. A
leveraged long is not a position to open on one of two trend systems.

### It is paid for out of the same capital floor

This is the part that could have gone wrong quietly. Before the sleeve, the
options book could reach 4.0% of simultaneous max loss and G1 hard-halts at
−4.0% — exactly flush. Adding a sleeve that can lose 1.2% *alongside* that puts
5.2% of reachable loss behind a 4% halt: the identical decoration bug this
project already fixed once in G3, reintroduced by addition.

So the ceiling is **derived**:

```python
SLEEVE_RISK_BUDGET_PCT = SLEEVE_NOTIONAL * SLEEVE_STOP_PCT / START_NAV   # 0.012
TAIL_RISK_BUDGET_PCT   = 1_122.0 / START_NAV                             # 0.01122
BOOK_RISK_CEILING_PCT  = (START_NAV - NAV_HARD_HALT) / START_NAV \
                         - SLEEVE_RISK_BUDGET_PCT \
                         - TAIL_RISK_BUDGET_PCT                          # 0.01678
MAX_POSITIONS_PER_UNDERLYING = int(BOOK_RISK_CEILING_PCT
                                   / MAX_POSITION_RISK_PCT)              # 1
```

1.678% + 1.200% + 1.122% = 4.0%, exactly G1's halt distance.
`test_every_carve_out_together_lands_exactly_on_the_halt_distance` asserts the
**equality**, not an inequality: `≤` would still pass if a later edit shrank
the book and left a percent of the floor unclaimed, and the property worth
protecting is that all three claimants sit flush against the halt — no
reachable loss beyond it, no idle room behind it.
**What the carve-outs cost is real and stated**: the options book drops from
three positions per name to one. Setting either `SLEEVE_NOTIONAL = 0` or
`TAIL_RISK_BUDGET_PCT = 0` hands back exactly that budget and no more — two
further tests assert it — so a feature nobody is running cannot leave the
options book permanently shrunk.

### One entry, and only one

The exit block runs before the entry block, which creates a trap: a sleeve
stopped out at 11:00 would be re-bought at 11:00, because a 4% drop does not
necessarily break the 50-day line that S3 gates on. That is not a subtle
inefficiency — the carve-out funds **exactly one** stop loss. Spend it twice
and the account carries 2.4% of sleeve risk behind a −4% halt that also has to
cover a 1.678% options book, which is the same decoration bug arriving by a
different route.

So `SLEEVE_ONE_SHOT` retires the sleeve the moment it closes, for any reason,
and the flag is persisted in `state/sleeve.json` because every cycle is a fresh
container. `_reconcile_sleeve` sets it too when it finds the GTC stop fired
overnight. Independently of the arithmetic, it is also the right trading call:
a stop that immediately re-buys is not a stop, and on a four-day horizon there
is no second trend to catch.

### The stop rests at the broker, and the exit cancels it first

Alpaca serves no resting stop on a multi-leg options position — which is why
`manage.py` polls. A **single equity leg can**, so the sleeve's 4% stop is
submitted GTC immediately on fill, priced off the **actual fill** rather than
the pre-trade quote (S5 approved a specific dollar loss, and a stop 4% below a
price we never traded at is not that stop). It is the only exit that works
while the agent is not running: cron covers 10:00–15:45 ET, and the gap risk is
overnight.

That creates a hazard worth naming. Two orders now want the same shares, so
`close_sleeve` **cancels the resting stop before it sells**, and if the cancel
fails it re-reads the order: a stop that already *filled* means the position is
already flat and there is nothing to sell. Selling anyway would leave the
account **short QQQ** — the one position this repo exists to make impossible.
`test_the_exit_cancels_the_resting_stop_before_it_sells` asserts the ordering,
not just the outcome. On the next cycle after a stop that would not place, the
protection is retried; and `_reconcile_sleeve` trusts the **broker's** share
count over our file, because a GTC stop can fire while no cycle is watching.

**The sleeve is additive and cannot take the options book down with it.** It is
entered last, after the options loop has already measured, gated and filled, and
a broker fault inside it is caught, journaled as `sleeve_error` and contained.

---

## Alpaca infrastructure

**Every order goes through the Alpaca CLI, and that is a finding, not a
preference.** MCP was the first choice and cannot place multi-leg orders: the
`legs` array arrives as a JSON string and fails pydantic validation
([alpaca-mcp-server#97](https://github.com/alpacahq/alpaca-mcp-server/issues/97)).
The CLI places the identical order correctly; a live 4-leg SPY condor returned
`status: accepted, order_class: mleg`, then cancelled clean.

Market reads go through the official `alpaca-py` SDK rather than MCP —
`OptionHistoricalDataClient` and `StockHistoricalDataClient` for the chain and
bars, `TradingClient` for contract metadata. Earlier drafts of this document
said reads went "through MCP"; they never did, and the claim is corrected here
rather than left to be discovered. The contest requires the Trading API plus
"MCP server and/or CLI", which the CLI write path satisfies on its own.

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

**The options book** harvests the variance risk premium with defined risk. It is
designed to be *right often and wrong small*: most weeks it collects a modest
credit, and its bad outcome is bounded by the wing width on every position.

**The tail position was a third thing, and it was the one trade here with
negative expected value. It is now CLOSED** -- sold 2026-09-01 at $2.83 for
$3,113 against $4,433 paid, realising -$1,320, which was the whole account
drawdown. It was closed once `research/strategy_backtest.py` showed no edge
anywhere in the book to justify paying ~15% over fair value to hold variance.
What follows is what it was and why it went on, kept rather than rewritten.
On 2026-09-01 the book bought 11 QQQ Sep-11 720 calls for $4,433.

**And a second tail went on the same evening.** 6 x TQQQ Sep-11 70C at $1.87
($1,122), placed on an explicit instruction after the evidence against it was
put on the record: a TQQQ gap-down bounce tests as noise over five years
(t = +0.42 at the matching gap size, and the mean turns negative at gaps below
-4%), and the calls cost about 1.4x realised vol. It is sized inside the
per-position cap and carved out of the book ceiling, so it breaks no gate --
but it consumed the last of the room in front of the capital floor, and the
entry ramp is therefore closed to zero for the remaining sessions. The floor
was held by stopping new risk, not by rewriting the cap that measures it. It is *long* premium: it pays the variance risk premium this
document argues is worth harvesting, at 15.2% implied against 12.07% realized
-- a 1.23x premium, about **-0.32% of NAV in expectation**. Calling it anything
else would be the one dishonest line in a submission whose whole claim is that
you can check it.

Two things are true about it at once. It repairs a real structural flaw: the
condor is short the SPY 781 call, so across the joint book a +3 sigma rally
paid *less* than +1 sigma (+0.11% against +0.22%) -- the book was short its own
upside, and long calls convert that into positive convexity. And it is a
variance decision taken under a contest whose payoff is convex in rank, the
same reasoning as the sleeve and disclosed the same way. Its loss is bounded by
the premium paid and cannot exceed it, which is why it carries no stop: a long
option needs none. It is not managed by the options book, and rather than
letting it read as an unmanaged leg, `config.ACKNOWLEDGED_SYMBOLS` names it so
the discrepancy check *reports* it every cycle instead of flagging it -- the
check still fires for anything genuinely unexplained.

**The sleeve is a different animal and we will not blur the two.** It is a
directional long. Its loss is *bounded* by a resting stop, not *defined* by a
long wing, and those are not the same guarantee: an overnight gap through the
stop turns it into a market sell at the gap price, so the realized loss can
exceed the $1,176 the budget models. That is the sleeve's real tail, it is
named in `SleeveCandidate.modeled_max_loss`, and it is the reason the whole
sleeve is capped at 1.2% of NAV rather than sized to what the trend systems
would justify.

What the two together mean for the distribution: the options book alone made
`P(>+2%)` negligible over the remaining sessions. QQQ's measured 20-day
volatility is 17.4%, giving a 3-day sigma of 1.90% on the sleeve's $29,398 —
about ±$559 at one sigma. So the sleeve roughly triples the width of the
outcome distribution in both directions. **It is not an edge claim. It is a
variance claim, taken deliberately, with a −4% floor underneath it.**

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

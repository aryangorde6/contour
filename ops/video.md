# The submission video — script, slides, and how to shoot it

**Target 4:05–4:15.** The rubric scores anything under 3:00 as "2 — Limited",
and the ceiling is 4:30. Do not run short to be safe; run long and cut.

Narration below is ~600 words, which is 4:05–4:15 at an unhurried 145 wpm.
**Read it slower than feels natural.** Every number marked ⚠️ is live and will
have changed by the time you record — re-read it off the dashboard first.

---

## Before you record

```bash
git pull && .venv/bin/python -m pytest -q          # expect 133 passed
.venv/bin/python -m contour --replay               # the money shot, rehearse it
```

Open in tabs, in this order, so you never fumble mid-take:

1. https://aryangorde6.github.io/contour/ — the dashboard
2. A terminal, font size 18+, in `~/alpaca_hack`, cleared
3. https://app.alpaca.markets — order history for `PA35XVXLIO0E`
4. The slide deck (see below), on the market-analysis slide

Record at 1920×1080. Screen capture with a voiceover beats a webcam talking
head for a technical judge — but put a 3-second title card with your name and
the team name at the top so "Presentation & Execution" has something human to
grade.

---

## The script

### 1 · Hook — 0:00–0:28 · *slide 1, then the dashboard structure map*

> Everyone in this hackathon is selling iron condors. A condor sells both
> wings unconditionally — which means half the time you're selling the
> underpriced side and calling it diversification.
>
> Contour measures the volatility surface first, and lets the measurement pick
> the structure. This is the whole idea, and it's four lines.

*On screen: the structure map on the dashboard, three tickers plotted over the
four zones.*

### 2 · The rule — 0:28–1:00 · *dashboard, zoom the map*

> Two numbers. `vrp_ratio` is ATM implied over ten-day realized — am I being
> paid at all? Below 1.30, nothing trades. `skew_z` is the twenty-five delta
> put-call IV gap, scored against a per-underlying prior — which side holds
> the premium?
>
> Puts rich, sell puts. Calls rich, sell calls. Both fair, sell both. Not rich
> enough, sell nothing. Right now SPY sits at 1.42 and QQQ at 1.17 ⚠️ — same
> week, same market, opposite decisions. That's the thesis: the choice shows
> up in the order history, not just the README.

### 3 · It is actually running — 1:00–1:30 · *dashboard top, then Alpaca tab*

> This is the live judged account, `PA35XVXLIO0E`, running unattended on
> GitHub Actions — a pre-open cycle that parses the day's event windows, then
> every fifteen minutes from ten to close, and a scheduled flatten on Thursday
> so nothing is open into Friday's payroll print.
>
> Two SPY condors on the book ⚠️, and here they are in Alpaca's own order
> history. Nothing on this page is a mock.

### 4 · The AI, and its leash — 1:30–2:10 · *slide 2: the may / may-never table*

> GLM-5 on Amazon Bedrock. Every wired output can only make the agent trade
> **less**, and that's structural, not a promise: `execute.py` never imports
> the model layer, so no model output can physically reach an order.
>
> It may name event windows to stand down in. It may return a size multiplier,
> clamped at one. It may veto a structure. It may never choose a strike, size
> a position, or price one — that's arithmetic, and language models shouldn't
> do arithmetic that money depends on.
>
> I picked the model by bake-off, not reputation. All six candidates returned
> valid output, so the tiebreak was blackout accuracy — and Nova Pro and
> Llama-4 both invented a Monday ISM window that would have stood the agent
> down on the week's one clear session.

### 5 · Twelve gates, and proof — 2:10–2:55 · *terminal: run `--replay` live*

> Twelve risk gates. Pure functions, no I/O, fixed order, evaluated before
> every order — and the reason is journaled whether the gate passes or fails,
> so a no-trade cycle is exactly as auditable as a trade.
>
> You don't have to take my word for any of it. This is `--replay`: a fixture
> of real SPY, QQQ and IWM quotes recorded mid-session, run through the same
> measurement, selection and gate code the live agent runs. No Alpaca account
> needed. Clone the repo and you get this same output.

*Let the terminal fill. Say nothing for two seconds — the twelve green gates
are the strongest image in the video.*

> All twelve pass on a SPY condor. QQQ and IWM refused: not paid enough.

### 6 · Alpaca, and one real finding — 2:55–3:20 · *slide 3*

> Every order goes through the Alpaca CLI, and that's a finding, not a
> preference. The MCP server can't place multi-leg orders — the legs array
> arrives as a JSON string and fails validation. That's issue 97, open since
> July. The CLI places the identical order correctly.
>
> And the journal is an append-only SHA-256 hash chain. The dashboard
> re-verifies it in your browser, with WebCrypto, and prints the same verdict
> the CLI does — so the audit trail doesn't rest on my word either.

### 7 · Market, and who else is here — 3:20–3:45 · *slides 4 and 5*

> The premium is real but it isn't uniform — 1.42 on SPY against 1.17 on QQQ
> today ⚠️ — and that dispersion is the entire opportunity. I trade three ETFs
> and not single names because I measured the friction: single-name weeklies
> cost forty to eighty dollars round trip against a thirty to forty-two dollar
> modeled edge. The trade loses to costs before it starts.
>
> Four other teams here describe an LLM proposing and deterministic gates
> disposing. That's table stakes now. None of them chooses *which* structure
> to sell from a measurement. That's the part that's mine.

### 8 · Money, roadmap, and an honest number — 3:45–4:12 · *slides 6 and 7*

> The sellable asset here isn't the alpha, it's the audit trail: a broker or
> an RIA needs a defensible record of why an automated system did what it did,
> and that's the layer I'd license. Signal-selling is a registration problem,
> not a business.
>
> Next is expiry laddering, learned skew priors instead of hard-coded ones,
> and a backtest harness — which is a data problem now, not a rewrite, because
> the recorder already exists.
>
> And the honest number: defined-risk premium selling is capped at the credit.
> Median outcome is under one percent for the week. Whoever posts the winning
> P&L will have won a coin flip. I optimised the four criteria that aren't luck.

*Final card: repo URL, dashboard URL, `PA35XVXLIO0E`.*

---

## The deck

Seven slides. Keep them near-wordless; the narration carries it.

**1 — Title.** *Contour — the measurement picks the structure.* Name, team
FluffyMargins, the four-line rule, account `PA35XVXLIO0E`.

**2 — The leash.** Two columns, "the model may" / "the model may never", from
`WRITEUP.md`. Footer: *`execute.py` never imports `mind.py`.*

**3 — Alpaca infrastructure.** CLI vs MCP (link issue 97), the three-rung
ladder, `reconcile()` reading actual fills, the hash chain. One line each.

**4 — Market analysis.**
- Short-premium is the crowded retail options trade; the crowd sells one
  structure regardless of the surface.
- Measured today: SPY VRP 1.42, QQQ 1.17, IWM 1.20 ⚠️ — the premium exists
  and is *not* uniform. Dispersion is the opportunity.
- Friction is the real gate on universe size: single-name weeklies $40–80
  round trip vs a $30–42 modeled edge; the three ETFs cost $8–20.
- Alpaca serves no earnings-date endpoint on any plan, so any single-name
  design hangs its most important gate on data that does not exist.

> If you want a citable market-size number on this slide, source it yourself
> and put the citation on the slide. I am not giving you one to read out —
> an unsourced statistic is the easiest thing for a judge to catch.

**5 — Competitive analysis.** Four named submissions in one column (Horizon
Blackline, VRP Engine, AEGIS-Q, EdgeStack) with their own words: LLM proposes,
gates dispose, journaled. Second column, one row: *chooses among four
structures from measured skew.* Third row: *runnable without our credentials —
`--replay`, and chain verification in the judge's browser.*

**6 — Revenue model.** Three tiers, most honest first:
- **The audit layer is the product.** Gate engine + hash-chained decision
  record, licensed to brokers and RIAs who must defend automated decisions.
- **Managed own-capital / prop**, where P&L is the revenue and no
  registration question arises.
- **Not** signal subscriptions — that is investment advice and needs
  registration. Saying this out loud is a credibility win, not a weakness.

**7 — Roadmap.** Now: 3 ETFs, one expiry, 15-minute cycle, 133 tests. Next:
expiry laddering and rolls; skew priors learned per underlying instead of
hard-coded; a backtest harness over recorded fixtures. Then: paid feed to close
the indicative-vs-NBBO gap; portfolio-level vega and gamma caps instead of
per-position max loss only; the `HALT` file becomes a kill switch with paging.

---

## Cutting, if you run over 4:30

In this order: the bake-off detail in §4 (keep the sentence, drop the models),
then the friction numbers in §7, then §6's second paragraph. **Never cut §5** —
the twelve green gates and `--replay` are the strongest technical evidence in
the submission.

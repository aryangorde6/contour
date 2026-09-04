# The submission video — script, slides, and how to shoot it

**lablab specifies no video length.** Verified 2026-09-02 against both
`alpaca-overview.md` and the live hackathon page: "Video presentation" is a
submission field under *Cover image and presentation*, but no minimum, maximum
or target duration is stated anywhere on it. Searching the page for
*video / demo / minute / presentation / recording / pitch* turns up no
requirement — the only "minute" on the whole page is inside another team's
project blurb.

An earlier revision of this file asserted a 4:05–4:15 target, a "2 — Limited"
score below 3:00, and a 4:30 ceiling. **None of that is sourced.** It has been
removed rather than left to be trusted. If the upload form imposes a cap when
you open it, use the cut table at the bottom.

So the length below is a **judgement, not a rule**: about four and three
quarter minutes, because that is what the content needs, and because the rubric line that governs it is *"how clearly
and effectively the project communicates its idea, **demonstrates the agent in
action**, and presents the reasoning behind it"* — which rewards the `--replay`
run in §5, not minutes.

Narration is **697 words**, so pace decides the runtime:

| Pace | Runtime |
|---|---|
| 155 wpm (brisk) | 4:30 |
| **145 wpm (planned)** | **4:48** |
| 135 wpm (unhurried) | 5:10 |
| 125 wpm (slow) | 5:35 |

Read it deliberately and **time a full take.** Every number marked ⚠️ is live
and will have changed — re-read it off the dashboard first.

**What changed in this revision, and why.** §7 is the volume-profile filter we
measured, shipped, and switched off on P&L evidence. §8 is now the
**attribution**: lablab's page marks P&L Performance as the primary judging
criterion, and the criterion's own wording asks for the P&L of *the submitted
agent*. This account holds two traders, the broker stamps which is which, and
§8 shows the split and the command that reproduces it. Those two sections are
the reason to watch this video rather than anyone else's — every other
submission will claim an edge, and this is the only one that tested its own,
reported the answer, and can prove which trades were even its idea.

Every timing and word count in this file is derived, not estimated —
`python ops/video_timing.py` recomputes them from the narration blockquotes,
and `--check` fails if they have drifted. Hand-estimates were 40% light.

---

## Before you record

```bash
git pull && .venv/bin/python -m pytest -q          # expect 294 passed, 1 skipped
.venv/bin/python -m contour --replay               # the money shot, rehearse it
```

Open in tabs, in this order, so you never fumble mid-take:

1. https://aryangorde6.github.io/contour/ — the dashboard
2. A terminal, font size 18+, in `~/alpaca_hack`, cleared
3. https://app.alpaca.markets — order history for `PA35XVXLIO0E`
4. **https://aryangorde6.github.io/contour/deck.html** — the deck, fullscreen

The deck is a 1280×720 stage scaled to the window, so it looks identical at
1080p and on a laptop; `←` `→` or click to move, slide number bottom-right. Its
market-analysis slide reads the agent's own published VRP measurement rather
than a number typed in last week, so it is current on the day you record
without you touching it.

Record at 1920×1080. Screen capture with a voiceover beats a webcam talking
head for a technical judge — but put a 3-second title card with your name and
the team name at the top so "Presentation & Execution" has something human to
grade.

---

## The script

Timings are cumulative and were measured, not estimated — the word counts below
are what `ops/` counts in the blockquotes, at 145 wpm. If a section runs long on
the stopwatch, cut inside that section rather than borrowing from §5 or §7.

### 1 · Hook — 0:00–0:21 · *slide 1, then the dashboard structure map* · 50w

> Everyone in this hackathon is selling iron condors. A condor sells both
> wings unconditionally — which means half the time you're selling the
> underpriced side and calling it diversification.
>
> Contour measures the volatility surface first, and lets the measurement pick
> the structure. That's the whole idea, and it's four lines.

### 2 · The rule — 0:21–0:48 · *dashboard, zoom the map* · 66w

> Two numbers. `vrp_ratio` is ATM implied over ten-day realized — am I being
> paid at all? Below 1.30, nothing trades. `skew_z` is the twenty-five-delta
> put-call IV gap against a per-underlying prior — which side holds the
> premium?
>
> Puts rich, sell puts. Calls rich, sell calls. Both fair, sell both. Not rich
> enough, nothing. On screen, SPY and QQQ disagree today ⚠️ — same market,
> opposite decisions.

### 3 · It is actually running — 0:48–1:09 · *dashboard top, then Alpaca tab* · 51w

> This is the live judged account, running unattended on GitHub Actions — a
> pre-open cycle that parses the day's event windows, then every fifteen
> minutes to the close.
>
> A SPY condor on the book right now ⚠️, and here it is in Alpaca's own order
> history. Nothing here is a mock.

### 4 · The AI, and its leash — 1:09–1:48 · *slide 2: the may / may-never table* · 95w

> GLM-5 on Amazon Bedrock. Every wired output can only make the agent trade
> **less** — structural, not a promise: `execute.py` never imports the model
> layer, so no model output can reach an order.
>
> It may name event windows to stand down in, veto a structure, or stand the
> book down. It may never choose a strike, size, or price one — that's
> arithmetic, and language models shouldn't do arithmetic money depends on.
>
> Sizing sat in that left column until I measured it — sixteen identical
> answers in a row. It went to trend systems instead.

### 5 · Nineteen gates, and proof — 1:48–2:30 · *terminal: run `--replay` live* · 101w

> Nineteen risk gates — twelve for the options book, seven for the sleeve.
> Pure functions, no I/O, evaluated before every order. The reason is journaled
> whether the gate passes **or** fails, so a no-trade cycle is exactly as
> auditable as a trade.
>
> You don't have to take my word for it. This is `--replay`: a fixture of real
> SPY, QQQ and IWM quotes run through the same measurement, selection and gate
> code the live agent runs. No Alpaca account needed — clone the repo, get this
> output.

*Let the terminal fill. Say nothing for two seconds — the green gates are the
strongest image in the video.*

> All twelve pass on a SPY condor. QQQ and IWM refused: not paid enough.

### 6 · Alpaca, and one real finding — 2:30–2:52 · *slide 3* · 52w

> Every order goes through the Alpaca CLI, and that's a finding, not a
> preference: the MCP server can't place multi-leg orders. The legs array
> arrives as a JSON string and fails validation — issue 97, open since July.
>
> And the journal is a SHA-256 hash chain the dashboard re-verifies in your
> browser.

### 7 · An edge I measured, shipped, and switched off — 2:52–3:36 · *terminal: `research/`* · 106w

**This is the section that differentiates the submission. Do not cut it.**

> Here's the part I'd want to see if I were judging. Delta picks strikes from a
> model; a volume profile measures where price has actually traded. Five years
> of bars said a call strike inside the value area gets touched thirty-three
> percent of the time against twenty-two outside — z of six-point-two.
>
> So I built it, and backtested it on three hundred eighty-seven cycles of real
> option prices. It cut P&L from plus nine-twenty-six to plus two-thirty, while
> avoiding not one single loss.
>
> So it's off. The code stays in the repo, because a robust signal pointed at
> the wrong objective is still a losing feature.

### 8 · The honest number — 3:36–4:32 · *slide 6: Performance, attributed* · 137w

**Do not cut this either.** P&L is the criterion the organisers highlight on
the page; this is the section that answers it.

> And the number itself. I backtested the whole book the same way: plus
> zero-point-nine percent over two and a half years, t of
> zero-point-three-seven. No edge — I won't claim a strategy works because it
> survived one week.
>
> The account is down about half a percent ⚠️. But the criterion asks for
> the *submitted agent*, and there are two traders in this account.
>
> Every order the agent places carries an ID it chose. The tail trades I took
> against my own recorded evidence don't. Split on that field: the operator is
> minus zero-point-six-four ⚠️; the agent is plus zero-point-one-five ⚠️ —
> flat, which is exactly what a backtest with a t of zero-point-three-seven
> predicts.
>
> That's a field the broker stamps, not my bookkeeping. `attribution.py
> --offline` — no credentials — reconciles it to broker equity within two
> dollars.

### 9 · Close — 4:32–4:48 · *slide 7, then the final card* · 39w

> So the sellable asset isn't the alpha — it's the audit trail. A broker or an
> RIAs need a defensible record of why an automated system did what it did.
>
> I'd rather show you a leash than a forecast.

*Final card: repo URL, dashboard URL, `PA35XVXLIO0E`.*

---

## Why this order

The first six sections earn the right to be believed; §7 and §8 spend it. A
judge who has just watched nineteen gates go green in a terminal they could run
themselves is primed to accept a negative result as rigour rather than as
weakness — and §8's "the account is down" lands very differently after §7
than it would cold at 0:30. Never move §8 earlier to "get it out of the
way": without §7 in front of it, it reads as an apology.

---

## The deck

**Built: `dashboard/deck.html`, live at
[aryangorde6.github.io/contour/deck.html](https://aryangorde6.github.io/contour/deck.html).**
Slide numbers match the counter in the corner.

**1 — Title.** *Contour — the measurement picks the structure.* Name, team
FluffyMargins, the four-line rule, account `PA35XVXLIO0E`.

**2 — The leash.** Two columns, "the model may" / "the model may never".
Footer: *`execute.py` never imports `mind.py`.*

**3 — Alpaca infrastructure.** CLI vs MCP (link issue 97), the three-rung
ladder, `reconcile()` reading actual fills, the hash chain, and the
resting-stop asymmetry: none on a multi-leg position, so the options book
polls; a single equity leg can rest one, so the sleeve does, GTC.

**4 — Market analysis.** The three VRP figures are **live** — fetched from
`state/surface.json` on the `agent-state` branch, with the last known values
baked in as a fallback so it never renders an em-dash on stage. Alongside:
friction is the real gate on universe size ($40–80 round trip on single names
against a $30–42 modelled edge, versus $8–20 on the ETFs), and Alpaca serves no
earnings-date endpoint on any plan, so a single-name design hangs its most
important gate on data that does not exist.

> If you want a citable market-size number on this slide, source it yourself
> and put the citation on the slide. I am not giving you one to read out — an
> unsourced statistic is the easiest thing for a judge to catch.

**5 — Competitive analysis.** Four named submissions (Horizon Blackline, VRP
Engine, AEGIS-Q, EdgeStack) in their own words: LLM proposes, gates dispose,
journaled. Second column: *chooses among four structures from measured skew.*
Third: *runnable without our credentials — `--replay`, and chain verification
in the judge's browser.* You are not narrating this slide any more; it is there
for the judge who pauses.

**6 — Performance, attributed.** The P&L split by `client_order_id`, with the
capture timestamp on the slide. `tests/test_attribution.py` pins the
`contour-` prefix the split rests on, and asserts this slide quotes the same
capture as `ops/order_history.json` — so a stale figure fails the suite rather
than ageing quietly. **Regenerate before you record:**
`python ops/attribution.py --publish` refreshes the export *and* rewrites the
stamp and figures in this slide, `WRITEUP.md` and `WRITEUP-ONEPAGE.md`. Then
re-read §8 — the spoken numbers there are hand-written and marked ⚠️.

**7 — Revenue model.** Three tiers, most honest first: the audit layer is the
product; managed own-capital / prop; and **not** signal subscriptions, which is
investment advice and needs registration. Saying that out loud is a credibility
win.

**8 — Roadmap.** Now: 3 ETFs, one expiry, 15-minute cycle, the $30k QQQ sleeve,
298 tests. Next: expiry laddering and rolls; skew priors learned per underlying
instead of hard-coded; **more backtest history** — the harness is built and has
run over 387 cycles, and Alpaca's option data starting 2024-01-18 is the binding
limit, not the code. Then: paid feed to close the indicative-vs-NBBO gap;
portfolio-level vega and gamma caps; the `HALT` file as a paging kill switch.

---

## Cutting, if you need it shorter

Take these **in order** — each row shows what the script drops to, and what that
runs at both paces. Cut before recording rather than rushing the ending; §8 and
§9 are where a rushed take does the most damage, because they are the two the
judge remembers.

| Cut | Saves | Script | @145 | @135 |
|---|---:|---:|---:|---:|
| §4's sizing anecdote | −21w | **676w** | 4:40 | 5:00 |
| §2's last sentence | −13w | **663w** | 4:34 | 4:55 |
| §3's schedule detail | −15w | **648w** | 4:28 | 4:48 |
| §6's issue-97 detail | −14w | **634w** | 4:22 | 4:42 |

1. **§4's sizing anecdote** — the whole "Sizing sat in that left column…" paragraph. The leash table is the point; the anecdote is supporting colour.
2. **§2's last sentence** — "On screen, SPY and QQQ disagree today…" — the map is already on screen saying it.
3. **§3's schedule detail** — keep "This is the live judged account, running unattended on GitHub Actions", drop the cycle timings.
4. **§6's issue-97 detail** — keep "the MCP server can't place multi-leg orders", drop the JSON-string diagnosis. **Never cut §6's second paragraph** — the hash chain is a differentiator, not filler.

**Never cut §5 or §7.** The green gates and `--replay` are the strongest
technical evidence in the submission, and the switched-off filter is the only
thing in it that no other team can say. If all four cuts still leave you over,
the problem is pace, not length — re-time a take before touching either.

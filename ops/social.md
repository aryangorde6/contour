# Five social posts

Two $500 prizes, and across 23 visible submissions there were ~18 likes in
total. This is the least contested points on the board.

**Rules that actually matter:** `@lablabai` and `@AlpacaHQ` must be **in the
post body**, not only in a reply or an image. On LinkedIn, @-mention the
**lablab.ai** and **Alpaca** company pages so they resolve to real links.

**Post one per day, Mon→Fri.** Five posts on one day reads as spam and only
the first gets seen. Each post below carries a *different* idea on purpose —
nobody engages with the same claim five times.

Attach the linked image to every post. A post with an image gets seen; a
post with a bare link gets throttled.

---

## 1 · Monday — the thesis

**Image:** the structure map from the dashboard (the four zones with SPY, QQQ,
IWM plotted).

> Everyone in the @lablabai × @AlpacaHQ hackathon is selling iron condors.
>
> A condor sells both wings unconditionally — so half the time you're selling
> the side that isn't rich.
>
> Contour measures 25-delta skew first and sells only the rich side.
>
> aryangorde6.github.io/contour

**LinkedIn version** — same opening, then add:

> Two numbers decide it. `vrp_ratio` is ATM implied over 10-day realized: am I
> being paid at all? Below 1.30 it trades nothing. `skew_z` is the 25-delta
> put/call IV gap against a per-underlying prior: which side holds the premium?
>
> Puts rich → sell puts. Calls rich → sell calls. Both fair → sell both. Not
> rich enough → sell nothing.
>
> Today SPY measured 1.42 and QQQ 1.17. Same week, same market, opposite
> decisions — and that dispersion is the entire opportunity.

---

## 2 · Tuesday — the finding other builders can use

This is the post most likely to travel. It is useful to strangers, which is
the only reliable engagement mechanic. **Link the actual issue.**

**Image:** terminal showing the CLI's `status: accepted, order_class: mleg`.

> Lost half a day so you don't have to: the Alpaca MCP server can't place
> multi-leg option orders. The `legs` array arrives as a JSON string and fails
> pydantic validation — alpaca-mcp-server#97, open since July.
>
> The CLI places the identical 4-leg order fine.
>
> @AlpacaHQ @lablabai

**LinkedIn version** — add:

> Worth saying plainly because the hackathon brief lists "MCP server and/or
> CLI": if your strategy is multi-leg, the CLI is not the fallback, it's the
> path. Reads still go through alpaca-py — option snapshots carry Greeks but
> no open_interest, which lives on the Trading API contract object, so those
> get merged.

---

## 3 · Wednesday — how the model got picked

**Image:** the three-row bake-off table (day / GLM-5 / Nova Pro / Llama-4).

> Picked the LLM for my @lablabai × @AlpacaHQ agent by bake-off, not reputation.
>
> Task: name today's macro blackouts. Monday had none.
>
> Nova Pro and Llama-4 both invented an ISM window, lifted from Tuesday —
> standing the agent down on the week's one clear day.
>
> GLM-5 returned zero.

**LinkedIn version** — add:

> All six candidates returned schema-valid output, so "does it hold the schema"
> discriminated nothing. The tiebreak had to be a task where I already knew the
> answer, on dates I could check by hand.
>
> Hallucinating *caution* is still hallucinating. A false blackout costs you
> the whole session, and it looks like prudence in the logs — which is exactly
> why it survives review.

---

## 4 · Thursday — don't trust it, check it

**Image:** the dashboard's chain banner reading `verified — chain intact`.

> Don't trust my P&L screenshot. Check the journal.
>
> Every decision my @lablabai × @AlpacaHQ agent makes lands in an append-only
> SHA-256 hash chain. The dashboard recomputes it in your browser and prints
> the same verdict the CLI does.
>
> Change one byte and it names the record.

**LinkedIn version** — add:

> The browser side is WebCrypto — no server, no library, nothing of mine
> between you and the hash.
>
> There's a second half to this. `python -m contour --replay` runs a fixture of
> real SPY/QQQ/IWM quotes, recorded mid-session, through the same measurement,
> selection and gate code the live agent runs — nineteen risk gates, every
> reason printed whether it passes or fails. No Alpaca account required.
>
> A trading agent you can't rerun is a screenshot. Clone it and get the same
> decisions back.

---

## 5 · Friday — the bug

Post-mortems outperform announcements, and this one is true. Do not soften it.

**Image:** the two-column was/now table from `state.md`.

> Bug of the week: my @lablabai × @AlpacaHQ agent opened two condors, then
> couldn't see them.
>
> `run_cycle(open_positions=())` — a defaulted argument nothing ever passed.
> Profit target, stop, breach and Thursday's flatten all iterated an empty
> tuple.
>
> All unit-tested. None reached.

**LinkedIn version** — add:

> The exit logic was written before go-live, precisely because Alpaca holds no
> resting stop on a multi-leg position. It was correct. It just never received
> the book, because each cron cycle is a fresh container and nothing wrote the
> positions down.
>
> The lesson I'm keeping: a defaulted argument nobody passes is invisible to
> every test that calls the callee directly. Test what the system *hands* its
> components, not just what the components do when you hand them things
> yourself.

---

## If you only post twice

Post **2** and **5**. The MCP finding is useful to strangers and the bug
post-mortem is honest — those are the two that get engagement from people who
are not judges.

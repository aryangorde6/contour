# Contour

**An autonomous options agent that measures the shape of the volatility surface
every 15 minutes and lets the measurement pick the structure** — put credit
spread, call credit spread, iron condor, or nothing — on SPY, QQQ and IWM.

Built for the [Alpaca AI Trading Agents Hackathon](https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon),
28 Aug – 4 Sep 2026. Paper trading only.

## The idea in one sentence

Everyone sells iron condors. An iron condor sells *both* wings unconditionally,
which means half the time you are selling the underpriced side. Contour measures
25-delta skew first and sells only the rich side — so some sessions the account
shows put spreads, some call spreads, some condors, and the dashboard shows the
measurement that chose.

## What we claim, and what we don't

We do **not** claim this strategy makes money. We backtested it on 387 cycles
of real historical option prices and got **+0.93% over two and a half years,
t = +0.37** — indistinguishable from zero, and unstable year to year
(`research/strategy_backtest.txt`). We also measured a volume-profile strike
filter that looked strong on touch probability (z = +6.20), shipped it, found
it *cut* P&L in the same backtest, and **switched it off**. Both the code and
the negative result are still in the repo.

What we do claim is that every statement here is checkable without our
credentials: `pytest` runs 296 tests, `python -m contour --replay` reproduces
the agent's decisions from a committed quote fixture, and
`python -m contour --verify` walks the hash chain that records them. The live
P&L is split by `client_order_id` into what the agent placed and what a human
did — the three discretionary tail trades account for the entire drawdown, and
`python ops/attribution.py --offline` recomputes the split from committed order
history with no credentials. Line by line in **[WRITEUP.md](WRITEUP.md)**.

## Writes through the CLI, and why

Deliberately, and worth stating plainly: Alpaca's MCP server **cannot currently
place multi-leg options orders**. Its `place_option_order` receives the `legs`
array as a JSON string and fails pydantic validation
([alpacahq/alpaca-mcp-server#97](https://github.com/alpacahq/alpaca-mcp-server/issues/97),
open since 2026-07-01). A direct REST POST of the identical payload returns 200,
and the CLI's generated `--order-class mleg --legs` path handles it correctly.
We sent the fix upstream as [#118](https://github.com/alpacahq/alpaca-mcp-server/pull/118).

So Contour writes every order through the Alpaca CLI, and reads the market
through the official `alpaca-py` SDK — option chain snapshots merged with
Trading API contract objects, because snapshots carry Greeks and quotes but no
`open_interest`.

## Status

Risk layer first, strategy second — deliberately.

- [x] `contour/config.py` — every threshold, single source of truth
- [x] `contour/models.py` — the data the gates reason over
- [x] `contour/gates.py` — G1–G12, twelve pure functions, zero I/O
- [x] `contour/journal.py` — append-only SHA-256 hash chain
- [x] `contour/surface.py` — atm_iv, rv10, vrp_ratio, skew25, skew_z
- [x] `contour/select.py` — the four-branch structure map
- [x] `contour/structures.py` — strike selection, sizing, signed limit price
- [x] `tests/` — 296 collected, 294 passing, 1 skipped by design
- [x] `contour/execute.py` — CLI broker, 3-rung ladder, fill reconciliation
- [x] `contour/manage.py` — exits, shorts-first legout, escalation
- [x] `contour/data.py` — DataSource seam (snapshots + contracts merged)
- [x] `contour/clock.py` — session phase; cron never trusts its firing time
- [x] `contour/loop.py` — one idempotent cycle
- [x] `contour/__main__.py` — `--once --dry --as-of --dev --verify`
- [x] `contour/llm.py` — provider seam; the vendor is a config value
- [x] `contour/sleeve.py` — the directional sleeve, S1–S7, zero I/O
- [x] `contour/regime.py` — position sizing from three published trend systems
- [x] `contour/mind.py` — the brain: blackout windows, structure veto, stand-down
- [x] `.github/workflows/` — the cron that actually trades
- [x] `dashboard/` — live state, hash chain verified in the browser
- [x] `contour/replay.py` — record a session, replay it with no keys

## The dashboard

**Live: [aryangorde6.github.io/contour](https://aryangorde6.github.io/contour/)**

_Submission write-up: [WRITEUP.md](WRITEUP.md) · engineering detail:
[TECHNICAL.md](TECHNICAL.md)_

`dashboard/index.html` is one static file with no build step and no backend. It
reads the agent's own published state from the orphan **`agent-state`** branch
over `raw.githubusercontent.com` and renders six things:

- **the structure map** — SPY, QQQ and IWM plotted on 25-delta skew z-score
  against the VRP ratio, over the four decision zones. The chart *is* the
  strategy: where a name lands decides what gets sold, or whether anything does.
- the surface measurement and every gate result, pass or fail
- **what sized the book** — the per-underlying regime weight and the three
  trend systems behind it, each with the term that bound it. No language model
  sets that number; every decision below carries the weight it was sized at,
  including the refusals.
- **the directional sleeve** — one long QQQ position with its own seven gates,
  the LRS weight that sized it, and the resting stop that bounds it. It buys
  variance, not edge, and the panel says so.
- the equity curve against the $100,000 starting NAV
- **the hash chain, recomputed in your browser.** The page does not take the
  agent's word for it. It fetches the raw journal, walks the chain with
  WebCrypto SHA-256 and prints the same verdict `python -m contour --verify`
  prints. Tamper with one byte on the branch and the badge goes red.

`tests/test_dashboard.py` runs that JavaScript under Node against a chain
Python wrote — including a deliberately tampered record — and asserts both
implementations return the *same* verdict, so the badge cannot quietly become
decoration.

It is published to two independent hosts, both redeployed on every push to
`main`: GitHub Pages, which depends on no DNS at all, and Vercel on
`contour.aryangorde.com` — so a registrar or certificate problem cannot take
both down at once.

Serve it locally the same way, no build step required:

```bash
python -m http.server -d dashboard 8899
```

The page needs **HTTPS**, not just a web server: the chain check calls
`crypto.subtle`, which browsers expose only in a secure context. `localhost`
counts; a bare IP over plain HTTP does not.

## Quickstart

```bash
uv venv --python 3.12 .venv && . .venv/bin/activate
uv pip install -e '.[dev]'
python -m pytest -q
python -m contour --replay        # no Alpaca account required
```

`--replay` runs a committed fixture — a real recorded chain of SPY, QQQ and IWM
quotes — through the exact measurement, selection and gate code the live agent
uses, and prints the decisions with every gate reason. The newest fixture was
captured at 14:30 ET on 2026-08-31: it puts a SPY iron condor through all
twelve gates, refuses QQQ and IWM on the volatility premium, and takes the
sleeve through its own seven. It forces dry mode and a degraded brain, so the
same fixture gives the same answer on any machine, on any day — and the
degraded brain is visible in the output, halving the sleeve's notional. Record a new one with `--record fixtures/NAME.json --dev`.

## Risk gates

The twelve options gates — and the seven the directional sleeve adds, which
share the same capital floor and kill switch rather than declaring their own —
are documented in [WRITEUP.md](WRITEUP.md), with the full
calibration history in [TECHNICAL.md](TECHNICAL.md). Two of them deviate
from the original design, both because writing the tests first proved the
original values would have stopped the agent from ever placing an order. Those
deviations are documented inline in `config.py` rather than quietly applied.

## License

MIT

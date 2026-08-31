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

## Writes through the CLI, and why

Deliberately, and worth stating plainly: Alpaca's MCP server **cannot currently
place multi-leg options orders**. Its `place_option_order` receives the `legs`
array as a JSON string and fails pydantic validation
([alpacahq/alpaca-mcp-server#97](https://github.com/alpacahq/alpaca-mcp-server/issues/97),
open since 2026-07-01). A direct REST POST of the identical payload returns 200,
and the CLI's generated `--order-class mleg --legs` path handles it correctly.

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
- [x] `tests/` — 108 passing
- [x] `contour/execute.py` — CLI broker, 3-rung ladder, fill reconciliation
- [x] `contour/manage.py` — exits, shorts-first legout, escalation
- [x] `contour/data.py` — DataSource seam (snapshots + contracts merged)
- [x] `contour/clock.py` — session phase; cron never trusts its firing time
- [x] `contour/loop.py` — one idempotent cycle
- [x] `contour/__main__.py` — `--once --dry --as-of --dev --verify`
- [x] `contour/llm.py` — provider seam; the vendor is a config value
- [x] `contour/mind.py` — the brain: blackout windows, regime multiplier, structure veto
- [x] `.github/workflows/` — the cron that actually trades
- [x] `dashboard/` — live state, hash chain verified in the browser
- [x] `contour/replay.py` — record a session, replay it with no keys

## The dashboard

**Live: [aryangorde6.github.io/contour](https://aryangorde6.github.io/contour/)**

_Submission write-up: [WRITEUP.md](WRITEUP.md) · engineering detail:
[TECHNICAL.md](TECHNICAL.md)_

`dashboard/index.html` is one static file with no build step and no backend. It
reads the agent's own published state from the orphan **`agent-state`** branch
over `raw.githubusercontent.com` and renders four things:

- **the structure map** — SPY, QQQ and IWM plotted on 25-delta skew z-score
  against the VRP ratio, over the four decision zones. The chart *is* the
  strategy: where a name lands decides what gets sold, or whether anything does.
- the surface measurement and every gate result, pass or fail
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
uses, and prints the decisions with every gate reason. It forces dry mode and a
degraded brain, so the same fixture gives the same answer on any machine, on
any day. Record a new one with `--record fixtures/NAME.json --dev`.

## Risk gates

The twelve gates are documented in [WRITEUP.md](WRITEUP.md), with the full
calibration history in [TECHNICAL.md](TECHNICAL.md). Two of them deviate
from the original design, both because writing the tests first proved the
original values would have stopped the agent from ever placing an order. Those
deviations are documented inline in `config.py` rather than quietly applied.

## License

MIT

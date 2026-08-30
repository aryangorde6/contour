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

## Reads through MCP, writes through the CLI

Deliberately, and worth stating plainly: Alpaca's MCP server **cannot currently
place multi-leg options orders**. Its `place_option_order` receives the `legs`
array as a JSON string and fails pydantic validation
([alpacahq/alpaca-mcp-server#97](https://github.com/alpacahq/alpaca-mcp-server/issues/97),
open since 2026-07-01). A direct REST POST of the identical payload returns 200,
and the CLI's generated `--order-class mleg --legs` path handles it correctly.

So Contour reads the market through MCP v2 and writes every order through the
Alpaca CLI. Both are exercised for real.

## Status

Risk layer first, strategy second — deliberately.

- [x] `contour/config.py` — every threshold, single source of truth
- [x] `contour/models.py` — the data the gates reason over
- [x] `contour/gates.py` — G1–G12, twelve pure functions, zero I/O
- [x] `contour/journal.py` — append-only SHA-256 hash chain
- [x] `contour/surface.py` — atm_iv, rv10, vrp_ratio, skew25, skew_z
- [x] `contour/select.py` — the four-branch structure map
- [x] `contour/structures.py` — strike selection, sizing, signed limit price
- [x] `tests/` — 32 passing
- [ ] `execute.py` · `manage.py` · `loop.py`
- [ ] dashboard, GitHub Actions cron, `--replay`

## Quickstart

```bash
uv venv --python 3.12 .venv && . .venv/bin/activate
uv pip install -e '.[dev]'
python -m pytest -q
```

## Risk gates

The twelve gates are documented in [WRITEUP.md](WRITEUP.md). Two of them deviate
from the original design, both because writing the tests first proved the
original values would have stopped the agent from ever placing an order. Those
deviations are documented inline in `config.py` rather than quietly applied.

## License

MIT

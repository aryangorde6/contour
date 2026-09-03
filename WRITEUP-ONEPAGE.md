# Contour — an options agent that lets the measurement pick the structure

**Alpaca AI Trading Agents Hackathon · Options Alpha Agents**
Paper account **`PA35XVXLIO0E`** ·
[Repo](https://github.com/aryangorde6/contour) ·
[Dashboard](https://aryangorde6.github.io/contour/) ·
[Full technical detail](https://github.com/aryangorde6/contour/blob/main/WRITEUP.md)

Everyone sells iron condors. A condor sells **both** wings unconditionally, so
half the time you are selling the underpriced side. Contour measures the
volatility surface first and sells only the rich side — SPY, QQQ and IWM, one
locked expiry:

```
vrp_ratio < 1.30  ->  NO_TRADE      implied is not rich enough to sell
skew_z >= +0.8    ->  PUT_SPREAD    puts rich  -- sell puts, not cheap calls
skew_z <= -0.8    ->  CALL_SPREAD   calls rich -- sell calls, not cheap puts
otherwise         ->  IRON_CONDOR   both fair  -- sell both
```

`vrp_ratio` is ATM implied over 10-day realized (*am I paid at all?*); `skew_z`
is the 25-delta put/call IV gap against a per-underlying prior (*which side
holds the premium?*).

## AI logic

GLM-5 on **Amazon Bedrock**. **Every wired model output can only make the
agent trade less** — structural, not convention: `execute.py` never imports
`mind.py`, so no model output can reach an order.

The model **may** name event windows to stand down in, veto a structure, or
stand the book down. It **may never** choose a strike, size a position, or
price one. That is arithmetic, and language models should not do arithmetic
that money depends on.

Failure policy is asymmetric. No brain configured → run *degraded*: half size,
hard-coded event table, no veto, because an agent that stops when a model is
absent is not autonomous. Off-schema → **fail closed**: veto, size zero. The
model was chosen by bake-off, not reputation.

## Risk gates

Nineteen pure functions — twelve for the options book (G1–G12), seven for the
directional sleeve (S1–S7). Zero I/O, fixed order, evaluated before every
order, and **the reason is journaled whether a gate passes or fails**, so a
no-trade cycle is exactly as auditable as a trade.

G1 blocks entries below $97,000 NAV and halts below $96,000. G3 caps book risk
at a derived ceiling and 1.25% of NAV per position. G6 treats a null delta or
IV as a hard veto, never coerced to zero. G11 confines entries to
10:05–15:15 ET.

The capital floor is arithmetic, not decoration: **book 1.678% + sleeve 1.200%
+ tail 1.122% = exactly the 4% halt distance** — asserted as an *equality*, so
neither reachable loss beyond the halt nor idle room behind it survives an
edit.

## Alpaca infrastructure implementation

**Writes go through the Alpaca CLI, and that is a finding.** The MCP server
cannot place multi-leg option orders: the `legs` array arrives as a JSON string
and fails pydantic validation
([alpaca-mcp-server#97](https://github.com/alpacahq/alpaca-mcp-server/issues/97),
open since 2026-07-01). The CLI places the identical order correctly — a live
4-leg SPY condor returned `status: accepted, order_class: mleg`.

Reads go through **`alpaca-py`**, merging chain snapshots with contract
objects — snapshots carry Greeks but no `open_interest`, which G5 screens on.
Entries go out as a three-rung limit ladder from mid toward the bid, never a
market order, and `reconcile()` reads per-leg `filled_qty`, because paper fills
partially and trusting the request puts legs out of ratio.

Autonomy is **GitHub Actions**: a pre-open cycle, a cycle every 15 minutes from
10:00–15:45 ET, and a scheduled Thursday flatten. The journal is an
**append-only SHA-256 hash chain** verified in CI, and the dashboard
re-verifies it in your browser with WebCrypto.

## Performance, attributed

The criterion asks for the performance of *the submitted agent*; this account
holds two. <!-- ATTRIBUTION-SNAPSHOT --> At **2026-09-03 19:00** UTC:

| Placed by | of start NAV |
|---|---:|
| **The agent** — every `contour-*` order id | **+0.15%** |
| The operator — three discretionary tail trades | **−0.46%** |

Every order this repository submits is prefixed `contour-` in `loop.py`;
nothing else in the account carries it, so the split is a field *the broker*
records. `python ops/attribution.py --offline` recomputes it from committed
order history with no credentials, reconciling to broker equity within $1.65.

---

**Checkable without our credentials.** `pytest` runs 295 tests; `--replay`
reproduces every decision from a committed quote fixture. We claim no edge: a
387-cycle backtest returned **+0.93% over 2.5 years, t = +0.37**.

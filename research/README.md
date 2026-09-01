# research/

The measurements that justify a decision the agent makes, kept runnable so the
claim can be checked rather than believed. Each script prints a table; the
`.txt` beside it is that table as recorded, so a reader can diff instead of
re-running, and re-run when they don't trust the diff.

Needs `ALPACA_DEV_API_KEY` / `ALPACA_DEV_SECRET_KEY` in `.env`. Read-only:
these touch the market-data endpoints only and cannot place an order.

## profile_edge

Does the volume-profile value area carry information the option delta does
not? `structures.assemble` picks short strikes at 0.13 delta, which is a
lognormal probability of finishing ITM. This asks whether the *traded*
distribution disagrees usefully.

Distance is held fixed in sigma units, so the strike is identical in both arms
and only the value area's position moves. Five years of daily bars, SPY/QQQ/IWM,
8-trading-day horizon.

```bash
python research/profile_edge.py            | tee research/profile_edge.txt
python research/profile_edge_robustness.py | tee research/profile_edge_robustness.txt
```

Result: on the CALL side, a strike inside the value area is touched 32.8% of
the time against 21.9% outside it (z = +6.20) at the 1.13-sigma distance the
agent actually sells. On the PUT side the same test returns the wrong sign and
no significance. `contour/profile.py` therefore filters calls only, and the
put-side null is why — not an oversight.

## strategy_backtest

Does the strategy make money? This drives the agent's OWN code —
`select.choose_structure`, `structures.assemble`, `structures.build` and
`manage.should_exit` — against real historical option prices. One cycle per
weekly expiry per name, entered 10 days out, walked forward daily to expiry.
Implied vol is solved from each contract's close and delta derived from it,
because historical option bars carry no greeks.

```bash
python research/strategy_backtest.py | tee research/strategy_backtest.txt
```

387 cycles, Jan 2024 – Aug 2026, 159 trades:

```
                        total P&L   PF     t      win rate
delta strikes only          +$926   1.09  +0.37   73.0%
+ volume-profile filter     +$230   1.02  +0.09   72.2%
```

**The honest reading: the strategy makes approximately nothing.** +0.93% over
two and a half years, t = +0.37, which is indistinguishable from zero. It is
also unstable — 2024 lost $2,546 (PF 0.53), 2025 made $3,475 (PF 2.38), 2026 is
flat to the dollar. One good year out of three is not an edge.

Two things cut the other way and are stated rather than buried. Exits are
evaluated on daily closes while the live agent polls every 15 minutes, so stops
overshoot their 2x trigger badly: the 43 stops lost $10,537 against $4,951 if
each had filled at its trigger. Closing that gap entirely would put the total at
+$6,512 — an upper bound, not a result, and untested. And Alpaca's option
history begins 2024-01-18, so the sample cannot include 2022, the worst regime
for a short-premium book. The number above is flattered by that omission.

The filter's result is why `PROFILE_ENABLED` is False.

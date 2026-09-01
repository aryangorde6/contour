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

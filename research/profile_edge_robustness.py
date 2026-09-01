"""Robustness on the call-side finding at d=1.13 sigma. Distance is matched by
construction (the strike is identical either way; only VAH's position moves),
so this asks whether the edge survives slicing by symbol, year and vol regime."""
import os, math, sys
from datetime import datetime, timezone, timedelta
import numpy as np
from dotenv import load_dotenv
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
load_dotenv()
from profile_edge import value_area, load, ztest, LOOKBACK, HORIZON

D = 1.13
per_sym, per_year, per_vol = {}, {}, {}

allrows = []
for sym in ("SPY", "QQQ", "IWM"):
    bars = load(sym)
    closes = np.array([b["c"] for b in bars]); rets = np.diff(np.log(closes))
    vols = []
    for t in range(LOOKBACK + 21, len(bars) - HORIZON):
        vols.append(float(np.std(rets[t-20:t], ddof=1)))
    vlo, vhi = np.percentile(vols, [33, 67])
    i = 0
    for t in range(LOOKBACK + 21, len(bars) - HORIZON):
        va = value_area(bars[t-LOOKBACK:t]); sig = vols[i]; i += 1
        if va is None or not sig > 0:
            continue
        poc, vah, val = va
        spot = bars[t]["c"]; fwd = bars[t+1:t+1+HORIZON]
        fhi = max(b["h"] for b in fwd)
        ku = spot*math.exp(D*sig*math.sqrt(HORIZON))
        breach = fhi >= ku; outside = ku > vah
        yr = bars[t]["t"].year
        vb = "low" if sig < vlo else ("high" if sig > vhi else "mid")
        allrows.append((sym, yr, vb, outside, breach))

def tab(name, keyf):
    print(f"\n--- by {name} ---")
    g = {}
    for sym, yr, vb, outside, breach in allrows:
        k = keyf(sym, yr, vb)
        r = g.setdefault(k, [0,0,0,0])
        if outside: r[2]+=int(breach); r[3]+=1
        else:       r[0]+=int(breach); r[1]+=1
    for k in sorted(g):
        ib,i_n,ob,o_n = g[k]
        ip = ib/i_n if i_n else float('nan'); op = ob/o_n if o_n else float('nan')
        print(f"  {str(k):8} inside {ip:6.1%} (n={i_n:5d})  outside {op:6.1%} (n={o_n:5d})"
              f"  edge {ip-op:+6.1%}  z {ztest(ib,i_n,ob,o_n):+5.2f}")

tab("symbol", lambda s,y,v: s)
tab("year",   lambda s,y,v: y)
tab("vol regime", lambda s,y,v: v)

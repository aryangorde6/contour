"""Split the account's P&L into what the agent did and what a human did.

The judging criterion is "the trading performance of the SUBMITTED AGENT".
The account holds both -- the agent's systematic book, and three discretionary
tail trades placed by hand against the agent's own recorded evidence -- so the
headline equity number answers a different question than the one being asked.

The split is not a matter of opinion or of trusting our journal. Every order
the agent submits carries a client_order_id built by `order_base_id` or
`sleeve_base_id` in loop.py, both of which hard-prefix `contour-`; exits derive
from the entry id and inherit it. An order without that prefix cannot have come
from this codebase. So the partition is a string test on a field the BROKER
records, and `tests/test_attribution.py` pins the prefix invariant so it cannot
drift.

    .venv/bin/python ops/attribution.py             # live, needs credentials
    .venv/bin/python ops/attribution.py --offline   # from the committed export
    .venv/bin/python ops/attribution.py --publish   # ... and update the documents

`--offline` exists for the same reason `--replay` does: a reader without our
credentials has to be able to reproduce the number. It recomputes the whole
table from `ops/order_history.json`, which the live mode writes -- every order
the account has ever filled, with the client_order_id attached to each.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXPORT = ROOT / "ops/order_history.json"

AGENT_PREFIX = "contour-"
START_EQUITY = 100_000.0
# Fills reconcile to broker equity only up to fees and the broker's own
# rounding. A drift wider than this is a bug in the attribution, not noise,
# and the script says so rather than printing a table that does not add up.
RECONCILE_TOLERANCE = 25.00


def _multiplier(symbol: str) -> float:
    """Options are quoted per share and settle per 100. OCC symbols are always
    longer than the six characters an equity ticker can reach."""
    return 100.0 if len(symbol) > 6 else 1.0


def fetch() -> dict:
    """Every filled order plus the current marks, straight from Alpaca."""
    import os

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest

    client = TradingClient(os.environ["ALPACA_API_KEY"],
                           os.environ["ALPACA_SECRET_KEY"], paper=True)
    account = client.get_account()

    orders: list[dict] = []

    def take(order, client_order_id: str) -> None:
        # A multi-leg parent carries the client_order_id and no symbol; its
        # legs carry the symbols. Attribute each leg to the parent's id, which
        # is the id the agent actually chose.
        if order.symbol and order.filled_qty and float(order.filled_qty) \
                and order.filled_avg_price is not None:
            orders.append({
                "submitted_at": str(order.submitted_at),
                "client_order_id": client_order_id,
                "symbol": order.symbol,
                "side": "sell" if "sell" in str(order.side).lower() else "buy",
                "qty": float(order.filled_qty),
                "price": float(order.filled_avg_price),
            })
        for leg in (order.legs or []):
            take(leg, client_order_id)

    for order in client.get_orders(GetOrdersRequest(
            status=QueryOrderStatus.ALL, limit=500, nested=True)):
        take(order, order.client_order_id)

    return {
        "account": account.account_number,
        "captured_at": str(account.created_at and __import__("datetime")
                           .datetime.now(__import__("datetime").timezone.utc)),
        "account_created_at": str(account.created_at),
        "start_equity": START_EQUITY,
        "equity": float(account.equity),
        "orders": sorted(orders, key=lambda o: o["submitted_at"]),
        "marks": {p.symbol: float(p.market_value)
                  for p in client.get_all_positions()},
    }


def attribute(snap: dict) -> dict:
    """Per-symbol P&L = net cash from every fill + what the position is worth
    now. Closed positions have no mark and net cash IS the realised P&L."""
    cash: dict[str, float] = {}
    agent: dict[str, bool] = {}
    ids: dict[str, set] = {}
    for o in snap["orders"]:
        sym = o["symbol"]
        sign = 1.0 if o["side"] == "sell" else -1.0
        cash[sym] = cash.get(sym, 0.0) + sign * o["qty"] * o["price"] \
            * _multiplier(sym)
        coid = o["client_order_id"] or ""
        ids.setdefault(sym, set()).add(coid)
        # A symbol touched by ANY non-agent order is not cleanly the agent's,
        # so it is attributed to the human. This rounds against us on purpose:
        # the number we publish should be the pessimistic one.
        agent[sym] = agent.get(sym, True) and coid.startswith(AGENT_PREFIX)

    rows = []
    for sym in sorted(cash, key=lambda s: cash[s] + snap["marks"].get(s, 0.0)):
        mark = snap["marks"].get(sym, 0.0)
        rows.append({
            "symbol": sym,
            "pnl": cash[sym] + mark,
            "open": sym in snap["marks"],
            "agent": agent[sym],
            "ids": sorted(ids[sym]),
        })
    return {
        "rows": rows,
        "agent_pnl": sum(r["pnl"] for r in rows if r["agent"]),
        "human_pnl": sum(r["pnl"] for r in rows if not r["agent"]),
        "total_pnl": sum(r["pnl"] for r in rows),
        "broker_pnl": snap["equity"] - snap["start_equity"],
    }


def render(snap: dict, att: dict) -> str:
    start = snap["start_equity"]
    out = [
        f"account {snap['account']}   opened {snap['account_created_at'][:10]}"
        f"   start ${start:,.0f}",
        f"captured {snap['captured_at'][:19]}Z",
        "",
        f"{'symbol':24}{'P&L':>11}  {'':8}{'placed by':>10}  ids",
    ]
    for r in att["rows"]:
        who = "agent" if r["agent"] else "HUMAN"
        state = "open" if r["open"] else "closed"
        out.append(f"{r['symbol']:24}{r['pnl']:>11,.2f}  {state:8}{who:>10}  "
                   f"{', '.join(r['ids'])[:52]}")
    out += [
        "",
        f"{'THE AGENT  (contour-* order ids)':40}"
        f"{att['agent_pnl']:>11,.2f}{att['agent_pnl'] / start:>10.2%}",
        f"{'A HUMAN    (everything else)':40}"
        f"{att['human_pnl']:>11,.2f}{att['human_pnl'] / start:>10.2%}",
        f"{'TOTAL':40}{att['total_pnl']:>11,.2f}"
        f"{att['total_pnl'] / start:>10.2%}",
        "",
        f"broker equity says {att['broker_pnl']:+,.2f}; fills say "
        f"{att['total_pnl']:+,.2f}; unexplained "
        f"{att['total_pnl'] - att['broker_pnl']:+,.2f} (fees and rounding)",
    ]
    return "\n".join(out)


# --- publishing -----------------------------------------------------------
# The split appears in three documents, and the marks move every session, so a
# refresh means editing three tables in two markup languages by hand. That is
# how the figures drifted the first time. `--publish` rewrites them from the
# export instead; tests/test_attribution.py then fails if any surface still
# disagrees, so a half-applied refresh cannot ship.
PUBLISHED = ("WRITEUP.md", "WRITEUP-ONEPAGE.md", "dashboard/deck.html")

# Every table names the same three rows. The label cell is matched and kept;
# the numeric cells are rebuilt -- however many of them a surface carries (the
# one-pager has room for the percentage only).
ROW_LABELS = (("The agent", "agent_pnl"), ("The operator", "human_pnl"),
              ("Account total", "total_pnl"))
STAMP = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}")


def _cells(pnl: float, start: float, minus: str) -> list[str]:
    money = f"{'-' if pnl < 0 else '+'}${abs(pnl):,.2f}"
    return [money.replace("-", minus), f"{pnl / start:+.2%}".replace("-", minus)]


def _publish_line(line: str, fills: list[str], html: bool) -> str:
    """Rewrite the numeric cells of one table row, keeping the label and
    whatever emphasis each cell already carried."""
    if html:
        seen = [0]

        def repl(m: re.Match) -> str:
            i, seen[0] = seen[0], seen[0] + 1
            if i == 0:                                   # the label cell
                return m.group(0)
            bold = m.group(2).strip().startswith("<b>")
            value = fills[i - 1]
            return f"{m.group(1)}<b>{value}</b></td>" if bold else \
                f"{m.group(1)}{value}</td>"

        return re.sub(r"(<td[^>]*>)(.*?)</td>", repl, line)

    cells = line.rstrip("\n").strip().strip("|").split("|")
    out = [cells[0]]
    for cell, value in zip(cells[1:], fills):
        out.append(f" **{value}** " if cell.strip().startswith("**")
                   else f" {value} ")
    return "|" + "|".join(out) + "|\n"


def publish(snap: dict, att: dict) -> list[str]:
    """Push this export's stamp and figures into every document that quotes
    them. Returns the files that changed."""
    stamp, start = snap["captured_at"][:16], snap["start_equity"]
    changed = []
    for rel in PUBLISHED:
        path = ROOT / rel
        before = path.read_text(encoding="utf-8")
        html = path.suffix == ".html"
        minus = "&minus;" if html else "\u2212"
        lines = before.splitlines(keepends=True)
        for i, line in enumerate(lines):
            if "ATTRIBUTION-SNAPSHOT" in line:
                lines[i] = STAMP.sub(stamp, line)
                continue
            if not (line.lstrip().startswith("|") or "<td" in line):
                continue
            for label, key in ROW_LABELS:
                if label in line:
                    fills = _cells(att[key], start, minus)
                    n = (len(re.findall(r"<td[^>]*>", line)) if html
                         else len(line.strip().strip("|").split("|"))) - 1
                    lines[i] = _publish_line(line, fills[-n:], html)
                    break
        after = "".join(lines)
        if after != before:
            path.write_text(after, encoding="utf-8")
            changed.append(rel)
    return changed


def main(argv: list[str]) -> int:
    offline = "--offline" in argv
    if offline:
        if not EXPORT.exists():
            print(f"no export at {EXPORT}; run without --offline first")
            return 2
        snap = json.loads(EXPORT.read_text(encoding="utf-8"))
    else:
        snap = fetch()
        EXPORT.write_text(json.dumps(snap, indent=1, sort_keys=True) + "\n",
                          encoding="utf-8")

    att = attribute(snap)
    print(render(snap, att))

    drift = abs(att["total_pnl"] - att["broker_pnl"])
    if drift > RECONCILE_TOLERANCE:
        print(f"\nFAIL: {drift:,.2f} unexplained, over the "
              f"{RECONCILE_TOLERANCE:,.2f} tolerance")
        return 1
    if not offline:
        print(f"\nwrote {EXPORT.relative_to(ROOT)} -- rerun with --offline to "
              f"reproduce this table with no credentials")
    if "--publish" in argv:
        changed = publish(snap, att)
        print("\nupdated " + (", ".join(changed) if changed
                              else "nothing -- the documents already agree"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

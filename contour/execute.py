"""Order submission through the Alpaca CLI.

Why the CLI and not MCP: the MCP server cannot marshal a multi-leg `legs`
array -- it arrives at the tool as a JSON string and fails pydantic validation
(alpacahq/alpaca-mcp-server#97, open since 2026-07-01). The CLI's generated
`--order-class mleg --legs` path handles it correctly. Verified end to end on
2026-08-30: a 4-leg SPY Sep-11 condor returned status accepted.

Why credentials are passed per invocation and profiles are never used: the CLI
warns that an ALPACA_API_KEY in the environment SILENTLY OVERRIDES an explicit
`-p <profile>` flag. Observed live -- `alpaca account get -p dev` returned the
judged account because the judged key was exported. Every call here therefore
carries its own credentials AND asserts the broker's account_number matches the
account we intended before any order is submitted.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from . import config as C
from . import structures as S
from .models import Candidate


class BrokerError(RuntimeError):
    pass


class WrongAccountError(BrokerError):
    """The safety net for the profile-override trap. Never downgrade this."""


@dataclass
class CLIBroker:
    api_key: str
    secret_key: str
    expected_account: str          # PA-prefixed. Asserted before every order.
    cli_path: str = os.path.expanduser("~/go/bin/alpaca")
    timeout_s: int = 30
    _verified: bool = field(default=False, init=False, repr=False)

    # --- plumbing --------------------------------------------------------
    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["ALPACA_API_KEY"] = self.api_key
        env["ALPACA_SECRET_KEY"] = self.secret_key
        return env

    def _run(self, args: Sequence[str]) -> tuple[int, Any, str]:
        proc = subprocess.run(
            [self.cli_path, *args, "-q"],
            env=self._env(), capture_output=True, text=True,
            timeout=self.timeout_s,
        )
        out = proc.stdout.strip()
        try:
            parsed: Any = json.loads(out) if out else None
        except json.JSONDecodeError:
            parsed = out
        return proc.returncode, parsed, proc.stderr.strip()

    # --- reads -----------------------------------------------------------
    def account(self) -> dict:
        rc, data, err = self._run(["account", "get"])
        if rc != 0 or not isinstance(data, dict):
            raise BrokerError(f"account get failed rc={rc}: {err or data}")
        return data

    def assert_account(self) -> str:
        """Must pass before anything is submitted. Cached after first success
        so the check costs one call per cycle, not one per order."""
        if self._verified:
            return self.expected_account
        got = str(self.account().get("account_number", ""))
        if got != self.expected_account:
            raise WrongAccountError(
                f"REFUSING TO TRADE: credentials point at {got!r}, "
                f"expected {self.expected_account!r}"
            )
        self._verified = True
        return got

    def get_order(self, order_id: str) -> dict:
        rc, data, err = self._run(["order", "get", "--order-id", order_id])
        if rc != 0 or not isinstance(data, dict):
            raise BrokerError(f"order get failed rc={rc}: {err or data}")
        return data

    def open_orders(self) -> list[dict]:
        rc, data, err = self._run(["order", "list", "--status", "open"])
        if rc != 0:
            raise BrokerError(f"order list failed rc={rc}: {err or data}")
        return data if isinstance(data, list) else []

    def positions(self) -> list[dict]:
        rc, data, err = self._run(["position", "list"])
        if rc != 0:
            raise BrokerError(f"position list failed rc={rc}: {err or data}")
        return data if isinstance(data, list) else []

    # --- writes ----------------------------------------------------------
    def submit_mleg(self, legs: list[dict], qty: int, limit_price: float,
                    client_order_id: str, dry_run: bool = False) -> dict:
        """One multi-leg limit order. limit_price is already signed by
        structures.net_limit_price -- negative is a credit."""
        self.assert_account()
        args = [
            "order", "submit",
            "--order-class", "mleg",
            "--qty", str(qty),
            "--type", "limit",
            "--time-in-force", "day",
            "--limit-price", f"{limit_price:.2f}",
            "--legs", json.dumps(legs, separators=(",", ":")),
            "--client-order-id", client_order_id,
        ]
        if dry_run:
            args.append("--dry-run")
        rc, data, err = self._run(args)
        if rc != 0:
            raise BrokerError(f"submit rc={rc}: {err or data}")
        if not isinstance(data, dict):
            raise BrokerError(f"submit returned non-object: {data!r}")
        return data

    def submit_equity(self, symbol: str, qty: int, side: str, order_type: str,
                      client_order_id: str, limit_price: float | None = None,
                      stop_price: float | None = None,
                      tif: str = "day", dry_run: bool = False) -> dict:
        """One single-leg equity order, for the directional sleeve.

        Deliberately NOT a market order by default: the same discipline the
        options book uses. The entry is a marketable limit at the ask, which
        fills like a market order on a liquid ETF but cannot print at a gap
        price if the book is momentarily empty.

        The protective leg IS a stop, and that is the point -- Alpaca supports
        no resting stop on a multi-leg options position (see `manage.py`), so
        the options book has to poll. A single equity leg can rest a GTC stop
        at the broker, which is the only exit that works while the agent is
        not running. It is the sleeve's answer to overnight gap risk.
        """
        self.assert_account()
        args = [
            "order", "submit",
            "--symbol", symbol,
            "--qty", str(qty),
            "--side", side,
            "--type", order_type,
            "--time-in-force", tif,
            "--client-order-id", client_order_id,
        ]
        if limit_price is not None:
            args += ["--limit-price", f"{limit_price:.2f}"]
        if stop_price is not None:
            args += ["--stop-price", f"{stop_price:.2f}"]
        if dry_run:
            args.append("--dry-run")
        rc, data, err = self._run(args)
        if rc != 0:
            raise BrokerError(f"equity submit rc={rc}: {err or data}")
        if not isinstance(data, dict):
            raise BrokerError(f"equity submit returned non-object: {data!r}")
        return data

    def cancel(self, order_id: str) -> None:
        rc, data, err = self._run(["order", "cancel", "--order-id", order_id])
        if rc != 0:
            raise BrokerError(f"cancel rc={rc}: {err or data}")


# --- fill reconciliation -------------------------------------------------
def reconcile(order: dict) -> dict:
    """Build position state from what ACTUALLY filled.

    Alpaca paper issues random ~10% partial fills. Trusting the requested qty
    puts condor legs out of ratio, which is how you end up accidentally naked.
    """
    filled = int(float(order.get("filled_qty") or 0))
    requested = int(float(order.get("qty") or 0))
    legs = order.get("legs") or []
    leg_fills = [
        {
            "symbol": l.get("symbol"),
            "filled_qty": int(float(l.get("filled_qty") or 0)),
            "filled_avg_price": (float(l["filled_avg_price"])
                                 if l.get("filled_avg_price") else None),
            "side": l.get("side"),
        }
        for l in legs
    ]
    ratios = {lf["filled_qty"] for lf in leg_fills} if leg_fills else set()
    return {
        "order_id": order.get("id"),
        "status": order.get("status"),
        "requested_qty": requested,
        "filled_qty": filled,
        "partial": 0 < filled < requested,
        "legs": leg_fills,
        # If the legs did not all fill equally the structure is not what we
        # designed and must be repaired before anything else happens.
        "legs_balanced": len(ratios) <= 1,
    }


TERMINAL = {"filled", "canceled", "expired", "rejected", "done_for_day"}


def submit_with_ladder(
    broker: CLIBroker,
    cand: Candidate,
    base_id: str,
    journal: Callable[[dict], Any],
    rung_seconds: int = C.LADDER_RUNG_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Three rungs, each progressively less greedy. Cancel and resubmit between
    rungs.

    The client_order_id MUST differ per rung: Alpaca returns HTTP 422
    'client_order_id must be unique' on reuse, so a ladder that reuses one id
    is guaranteed to be rejected on rung two. The shared <base> still gives
    idempotent retry semantics within a rung.
    """
    legs = S.to_cli_legs(cand, closing=False)
    prices = S.ladder_prices(cand.net_credit)

    for i, price in enumerate(prices, start=1):
        coid = f"{base_id}-r{i}"
        preflight = broker.submit_mleg(legs, cand.contracts, price, coid,
                                       dry_run=True)
        journal({"event": "preflight", "rung": i, "client_order_id": coid,
                 "limit_price": price, "body": preflight})

        order = broker.submit_mleg(legs, cand.contracts, price, coid)
        oid = order.get("id")
        journal({"event": "submitted", "rung": i, "order_id": oid,
                 "client_order_id": coid, "limit_price": price,
                 "status": order.get("status")})

        waited = 0.0
        while waited < rung_seconds:
            sleep(min(5.0, rung_seconds - waited))
            waited += 5.0
            cur = broker.get_order(oid)
            if cur.get("status") in TERMINAL:
                break

        cur = broker.get_order(oid)
        rec = reconcile(cur)
        if rec["filled_qty"] > 0:
            if cur.get("status") not in TERMINAL:
                # A partial fill leaves the REMAINDER working at the broker.
                # We are about to write down a position of rec["filled_qty"]
                # contracts and stop watching this order, so anything that
                # fills after we look away is risk nobody manages -- the exact
                # failure the position book exists to prevent. Cancel first,
                # then re-read: the cancel races the book, and whatever filled
                # inside that window is ours whether we wanted it or not.
                try:
                    broker.cancel(oid)
                except BrokerError as exc:            # already terminal, fine
                    journal({"event": "residual_cancel_failed", "rung": i,
                             "order_id": oid, "error": str(exc)})
                cur = broker.get_order(oid)
                rec = reconcile(cur)
                journal({"event": "residual_canceled", "rung": i,
                         "order_id": oid, "filled_qty": rec["filled_qty"],
                         "requested_qty": rec["requested_qty"]})
            journal({"event": "filled", "rung": i, **rec})
            if not rec["legs_balanced"]:
                # Alpaca fills an mleg atomically across its legs, so this
                # should be unreachable -- which is precisely why it has to be
                # loud rather than assumed. Unequal leg quantities mean the
                # thing at the broker is not the defined-risk structure we
                # designed, and a short without its wing is naked. The caller
                # stops opening anything else this cycle; the book is repaired
                # with ops/repair_book.py, not traded out of.
                journal({"event": "unbalanced_fill", "rung": i,
                         "order_id": oid, "legs": rec["legs"],
                         "reason": "legs filled in unequal quantities -- the "
                                   "structure at the broker is not the one "
                                   "designed; repair before the next entry"})
            return rec

        if cur.get("status") not in TERMINAL:
            broker.cancel(oid)
        journal({"event": "rung_expired", "rung": i, "order_id": oid,
                 "limit_price": price})

    journal({"event": "no_fill", "base_id": base_id,
             "reason": "all three rungs expired unfilled"})
    return {"order_id": None, "status": "NO_FILL", "filled_qty": 0,
            "requested_qty": cand.contracts, "partial": False,
            "legs": [], "legs_balanced": True}


def submit_sleeve_entry(broker, cand, base_id: str,
                        journal: Callable[[dict], Any],
                        wait_s: int = C.SLEEVE_FILL_WAIT_S,
                        sleep: Callable[[float], None] = time.sleep) -> dict:
    """Buy the sleeve, then rest a GTC stop on whatever actually filled.

    Two things here are deliberate and load-bearing.

    *The stop is priced off the FILL, not off the pre-trade spot.* A stop
    placed 4% below a quote we never traded at is not the 4% stop the risk
    budget was derived from, and S5 approved a specific dollar loss.

    *A partial fill is cancelled before the stop is placed.* Otherwise the
    remainder keeps working at the broker while a stop sized for the filled
    portion rests underneath it -- and the position ends up larger than the
    thing protecting it. Same failure the options ladder cancels residuals
    for, one asset class over.
    """
    limit = round(cand.spot * (1.0 + C.SLEEVE_ENTRY_SLIP), 2)
    coid = f"{base_id}-e"

    pre = broker.submit_equity(cand.underlying, cand.shares, "buy", "limit",
                               coid, limit_price=limit, dry_run=True)
    journal({"event": "sleeve_preflight", "client_order_id": coid,
             "symbol": cand.underlying, "qty": cand.shares,
             "limit_price": limit, "body": pre})

    order = broker.submit_equity(cand.underlying, cand.shares, "buy", "limit",
                                 coid, limit_price=limit)
    oid = order.get("id")
    journal({"event": "sleeve_submitted", "order_id": oid,
             "client_order_id": coid, "symbol": cand.underlying,
             "qty": cand.shares, "limit_price": limit,
             "status": order.get("status")})

    waited = 0.0
    while waited < wait_s:
        sleep(min(5.0, wait_s - waited))
        waited += 5.0
        if broker.get_order(oid).get("status") in TERMINAL:
            break

    cur = broker.get_order(oid)
    filled = int(float(cur.get("filled_qty") or 0))
    if filled <= 0:
        if cur.get("status") not in TERMINAL:
            broker.cancel(oid)
        journal({"event": "sleeve_no_fill", "order_id": oid,
                 "reason": f"unfilled after {wait_s}s at ${limit:.2f}"})
        return {"order_id": oid, "filled_qty": 0, "fill_price": None,
                "stop_price": None, "stop_order_id": None,
                "status": cur.get("status")}

    if cur.get("status") not in TERMINAL:
        try:
            broker.cancel(oid)
        except BrokerError as exc:                          # already terminal
            journal({"event": "sleeve_residual_cancel_failed",
                     "order_id": oid, "error": str(exc)})
        cur = broker.get_order(oid)
        filled = int(float(cur.get("filled_qty") or 0))
        journal({"event": "sleeve_residual_canceled", "order_id": oid,
                 "filled_qty": filled, "requested_qty": cand.shares})

    fill_px = float(cur.get("filled_avg_price") or limit)
    stop_px = round(fill_px * (1.0 - C.SLEEVE_STOP_PCT), 2)
    journal({"event": "sleeve_filled", "order_id": oid, "filled_qty": filled,
             "requested_qty": cand.shares, "fill_price": fill_px,
             "partial": filled < cand.shares})

    stop_id = place_sleeve_stop(broker, cand.underlying, filled, stop_px,
                                f"{base_id}-s", journal)
    return {"order_id": oid, "filled_qty": filled, "fill_price": fill_px,
            "stop_price": stop_px, "stop_order_id": stop_id,
            "status": cur.get("status")}


def place_sleeve_stop(broker, symbol: str, qty: int, stop_price: float,
                      client_order_id: str,
                      journal: Callable[[dict], Any]) -> str | None:
    """The resting protective order. Returns its id, or None if it would not
    place.

    A failure here is NOT fatal and is deliberately not treated as one: the
    polling exit still bounds the position during market hours. It is loud,
    because what is lost is overnight protection specifically, and the cycle
    retries on the next pass -- an unprotected position that stays unprotected
    because nobody looked again is the failure worth engineering against.
    """
    try:
        so = broker.submit_equity(symbol, qty, "sell", "stop",
                                  client_order_id, stop_price=stop_price,
                                  tif="gtc")
    except BrokerError as exc:
        journal({"event": "sleeve_stop_failed", "symbol": symbol,
                 "stop_price": stop_price, "error": str(exc),
                 "reason": "position is UNPROTECTED overnight; the polling "
                           "exit still applies during market hours and the "
                           "next cycle retries"})
        return None
    journal({"event": "sleeve_stop_placed", "order_id": so.get("id"),
             "symbol": symbol, "qty": qty, "stop_price": stop_price,
             "tif": "gtc", "status": so.get("status")})
    return so.get("id")


def close_sleeve(broker, pos, spot: float, base_id: str,
                 journal: Callable[[dict], Any],
                 escalate: bool = False) -> dict:
    """Sell the sleeve out. THE RESTING STOP IS CANCELLED FIRST, always.

    Both orders want the same shares. Leave the stop working and the exit
    either gets rejected for insufficient quantity or -- if the stop triggers
    in the same instant -- both fill and the account is SHORT QQQ, which is
    the one position this entire repo is built to make impossible. Cancel,
    then sell.
    """
    if pos.stop_order_id:
        try:
            broker.cancel(pos.stop_order_id)
            journal({"event": "sleeve_stop_canceled",
                     "order_id": pos.stop_order_id})
        except BrokerError as exc:
            # Already filled or already gone. Re-reading tells us which, and
            # a stop that FILLED means the position is already flat.
            journal({"event": "sleeve_stop_cancel_failed",
                     "order_id": pos.stop_order_id, "error": str(exc)})
            try:
                st = broker.get_order(pos.stop_order_id)
            except BrokerError:
                st = {}
            if int(float(st.get("filled_qty") or 0)) > 0:
                journal({"event": "sleeve_already_stopped_out",
                         "order_id": pos.stop_order_id,
                         "fill_price": st.get("filled_avg_price"),
                         "reason": "the resting stop had already filled; "
                                   "nothing left to sell"})
                return {"closed": True, "order_id": pos.stop_order_id,
                        "via": "resting_stop"}

    slip = (C.SLEEVE_EXIT_SLIP_ESCALATED if escalate else C.SLEEVE_EXIT_SLIP)
    limit = round(spot * (1.0 - slip), 2)
    coid = f"{base_id}-x"
    try:
        order = broker.submit_equity(pos.underlying, pos.shares, "sell",
                                     "limit", coid, limit_price=limit)
    except BrokerError as exc:
        journal({"event": "sleeve_close_failed", "error": str(exc),
                 "limit_price": limit})
        return {"closed": False, "order_id": None, "via": None}
    journal({"event": "sleeve_close_submitted", "order_id": order.get("id"),
             "client_order_id": coid, "qty": pos.shares,
             "limit_price": limit, "escalated": escalate,
             "status": order.get("status")})
    return {"closed": True, "order_id": order.get("id"), "via": "limit"}

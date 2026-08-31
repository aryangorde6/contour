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

"""The open book, persisted across cron runs.

Every cycle is a fresh GitHub Actions container: nothing survives in memory.
So a position opened at 10:09 is invisible at 10:24 unless it is written down.
Without this file `run_cycle` receives an empty `open_positions`, which makes
every exit rule and every cross-cycle risk gate dead code -- the profit target,
the stop, the breach rule and the scheduled Thursday flatten all iterate an
empty tuple, and `Book(positions=())` reports zero open risk no matter what
the account actually holds.

It lives under `state.ROOT`, which the agent workflow already restores from
and publishes to the `agent-state` branch, so it needs no workflow change.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Sequence

from . import state
from .manage import ManagedPosition
from .models import Candidate, Leg

NAME = "positions"


def _leg(d: dict[str, Any]) -> Leg:
    return Leg(
        symbol=d["symbol"], side=d["side"], ratio_qty=d["ratio_qty"],
        option_type=d["option_type"], strike=d["strike"],
        expiration_date=date.fromisoformat(str(d["expiration_date"])),
        bid=d["bid"], ask=d["ask"], delta=d["delta"],
        implied_volatility=d["implied_volatility"],
        open_interest=d["open_interest"], tradable=d["tradable"],
        close_price=d["close_price"], quote_age_s=d.get("quote_age_s"),
    )


def _candidate(d: dict[str, Any]) -> Candidate:
    return Candidate(
        underlying=d["underlying"], structure=d["structure"],
        legs=tuple(_leg(l) for l in d["legs"]),
        net_credit=d["net_credit"], wing_width=d["wing_width"],
        contracts=d["contracts"],
        max_loss_per_contract=d["max_loss_per_contract"],
    )


def to_dict(p: ManagedPosition) -> dict[str, Any]:
    return {
        "candidate": asdict(p.candidate),
        "credit_received": p.credit_received,
        "opened_at": p.opened_at.isoformat(),
        "order_id": p.order_id,
    }


def from_dict(d: dict[str, Any]) -> ManagedPosition:
    return ManagedPosition(
        candidate=_candidate(d["candidate"]),
        credit_received=d["credit_received"],
        opened_at=datetime.fromisoformat(d["opened_at"]),
        order_id=d["order_id"],
    )


def load() -> list[ManagedPosition]:
    """Never raise. A corrupt file must not stop the cycle -- but it must not
    silently read as "no positions" either, because that is exactly the state
    that disables every exit. The caller logs the discrepancy against the
    broker's own position list."""
    p = Path(state.ROOT) / f"{NAME}.json"
    try:
        raw = json.loads(p.read_text())
    except Exception:                                        # noqa: BLE001
        return []
    out: list[ManagedPosition] = []
    for d in raw if isinstance(raw, list) else []:
        try:
            out.append(from_dict(d))
        except Exception:                                    # noqa: BLE001
            continue
    return out


def save(positions: Sequence[ManagedPosition]) -> Path:
    return state.write(NAME, [to_dict(p) for p in positions])


def credit_from_fill(rec: dict[str, Any], fallback: float) -> float:
    """Credit actually received per contract, from the per-leg fill prices.

    `manage.py` documents this as "actual fill not mid" for a reason: the stop
    fires at 2.0x the credit, so overstating the credit by using the mid makes
    the stop trigger later and take more loss than the design allows.
    """
    legs = rec.get("legs") or []
    total = 0.0
    seen = False
    for l in legs:
        px, qty = l.get("filled_avg_price"), l.get("filled_qty") or 0
        if px is None or not qty:
            continue
        seen = True
        # Selling brings credit in, buying pays it back out. Every leg we build
        # has ratio_qty 1 and Alpaca reports filled_avg_price per contract, so
        # the signed sum IS the per-contract credit -- verified against a live
        # fill: 1.25 - 0.92 + 0.78 - 0.31 = 0.80.
        total += px if str(l.get("side", "")).startswith("sell") else -px
    return abs(total) if seen else fallback


# --- the directional sleeve ----------------------------------------------
# Same reasoning as above, same failure mode if it is missing: a fresh
# container every cycle means a sleeve opened at 10:09 is invisible at 10:24,
# and an invisible position is an unmanaged one. It gets its own file rather
# than a row in `positions.json` because it is not a ManagedPosition -- it has
# shares and a stop price where the others have legs and a credit, and forcing
# one shape over both is how a share of QQQ ends up being asked for its wing
# width.
SLEEVE_NAME = "sleeve"


def sleeve_to_dict(p) -> dict[str, Any]:
    return {
        "underlying": p.underlying, "shares": p.shares,
        "entry_price": p.entry_price, "stop_price": p.stop_price,
        "opened_at": p.opened_at.isoformat(), "order_id": p.order_id,
        "stop_order_id": p.stop_order_id,
    }


def sleeve_from_dict(d: dict[str, Any]):
    from .sleeve import SleevePosition
    return SleevePosition(
        underlying=d["underlying"], shares=int(d["shares"]),
        entry_price=float(d["entry_price"]),
        stop_price=float(d["stop_price"]),
        opened_at=datetime.fromisoformat(d["opened_at"]),
        order_id=str(d["order_id"]),
        stop_order_id=(str(d["stop_order_id"])
                       if d.get("stop_order_id") else None),
    )


def load_sleeve():
    """Never raise, and never invent. A corrupt file reads as "no sleeve",
    which is safe here in a way it is not for the options book: the sleeve's
    protective stop rests AT THE BROKER, so a forgotten position is still
    bounded. The cycle logs the discrepancy against the broker's own share
    count regardless."""
    p = Path(state.ROOT) / f"{SLEEVE_NAME}.json"
    try:
        raw = json.loads(p.read_text())
        pos = raw.get("position") if isinstance(raw, dict) else None
        return sleeve_from_dict(pos) if pos else None
    except Exception:                                        # noqa: BLE001
        return None


def save_sleeve(pos, panel: dict[str, Any] | None = None,
                retired: bool = False) -> Path:
    """The single writer of `state/sleeve.json`.

    Position and dashboard panel go out together in one write, so the two can
    never disagree about whether a sleeve is open -- which is what would
    happen if the cycle published a panel and the book saved a position into
    the same file independently.
    """
    return state.write(SLEEVE_NAME, {
        "position": sleeve_to_dict(pos) if pos is not None else None,
        "retired": bool(retired),
        **(panel or {}),
    })


def sleeve_retired() -> bool:
    """Has the sleeve already had its one entry?

    Defaults to False on a missing file -- a first cycle has not spent the
    carve-out. It defaults to False on a CORRUPT one too, which is the less
    obvious call: the alternative is a parse error silently retiring a sleeve
    that never traded, and the gates still stand between that and an order.
    """
    p = Path(state.ROOT) / f"{SLEEVE_NAME}.json"
    try:
        raw = json.loads(p.read_text())
        return bool(raw.get("retired")) if isinstance(raw, dict) else False
    except Exception:                                        # noqa: BLE001
        return False

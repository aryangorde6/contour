"""The snapshot the dashboard reads. Plain JSON, committed by the agent."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path("state")


def write(name: str, payload: Any) -> Path:
    ROOT.mkdir(parents=True, exist_ok=True)
    p = ROOT / f"{name}.json"
    p.write_text(json.dumps(payload, indent=1, default=str) + "\n")
    _stamp(name)
    return p


def _stamp(name: str) -> None:
    """Record when each file was last actually rewritten.

    The heartbeat updates every cycle including CLOSED ones, but surface and
    decisions only change on a TRADE cycle. Without a per-file time the
    dashboard labels Thursday's last measurement with Sunday's heartbeat and
    reports it as "measured 2m ago" for the whole judging window.
    """
    p = ROOT / "written_at.json"
    try:
        d = json.loads(p.read_text())
        if not isinstance(d, dict):
            d = {}
    except Exception:                                        # noqa: BLE001
        d = {}
    d[name] = datetime.now(timezone.utc).isoformat()
    p.write_text(json.dumps(d, indent=1, sort_keys=True) + "\n")


def heartbeat(cycle: int, mode: str, reason: str, extra: dict | None = None) -> Path:
    return write("heartbeat", {
        "last_cycle_utc": datetime.now(timezone.utc).isoformat(),
        "cycle_count": cycle, "mode": mode, "reason": reason,
        **(extra or {}),
    })


def next_cycle() -> int:
    """The cycle ordinal, counted across containers rather than inside one.

    Every cron run is a fresh container, so an in-process counter is always 0
    -- which is exactly what the heartbeat and every journal record published
    so far reported. The previous heartbeat is the only thing that survives,
    so it is what we count from. A missing or corrupt heartbeat restarts at 1
    rather than raising: a wrong ordinal is cosmetic, a failed cycle is not.
    """
    try:
        d = json.loads((ROOT / "heartbeat.json").read_text())
        return int(d.get("cycle_count", 0)) + 1
    except Exception:                                            # noqa: BLE001
        return 1


def point(series: str, sample: dict[str, Any], cap: int = 600) -> Path:
    """Append one timestamped sample to a series the dashboard plots.

    Deliberately tolerant: a corrupt or missing history file restarts the
    series rather than raising. A cosmetic curve is never worth failing a
    trading cycle over.
    """
    p = ROOT / f"{series}.json"
    try:
        prev = json.loads(p.read_text())
        if not isinstance(prev, list):
            prev = []
    except Exception:                                            # noqa: BLE001
        prev = []
    prev.append({"t": datetime.now(timezone.utc).isoformat(), **sample})
    return write(series, prev[-cap:])

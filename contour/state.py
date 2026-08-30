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
    return p


def heartbeat(cycle: int, mode: str, reason: str, extra: dict | None = None) -> Path:
    return write("heartbeat", {
        "last_cycle_utc": datetime.now(timezone.utc).isoformat(),
        "cycle_count": cycle, "mode": mode, "reason": reason,
        **(extra or {}),
    })

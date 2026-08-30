"""Append-only, hash-chained decision log.

Every cycle writes a record -- including no-trade cycles, including gate
passes. The chain is what lets a judge reconcile our claims line-for-line
against the order history they pull from Alpaca independently.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

GENESIS = "0" * 64


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def link_hash(prev_hash: str, payload: dict[str, Any]) -> str:
    return hashlib.sha256((prev_hash + _canonical(payload)).encode()).hexdigest()


@dataclass(frozen=True)
class Record:
    seq: int
    prev_hash: str
    hash: str
    payload: dict[str, Any]

    def to_line(self) -> str:
        return json.dumps(
            {"seq": self.seq, "prev_hash": self.prev_hash,
             "hash": self.hash, "payload": self.payload},
            sort_keys=True, separators=(",", ":"), default=str,
        )


class Journal:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _tail(self) -> tuple[int, str]:
        if not self.path.exists():
            return -1, GENESIS
        last = None
        for line in self.path.read_text().splitlines():
            if line.strip():
                last = json.loads(line)
        if last is None:
            return -1, GENESIS
        return last["seq"], last["hash"]

    def append(self, payload: dict[str, Any]) -> Record:
        seq, prev = self._tail()
        rec = Record(seq + 1, prev, link_hash(prev, payload), payload)
        with self.path.open("a") as fh:
            fh.write(rec.to_line() + "\n")
        return rec

    def read(self) -> Iterator[Record]:
        if not self.path.exists():
            return
        for line in self.path.read_text().splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            yield Record(d["seq"], d["prev_hash"], d["hash"], d["payload"])

    def verify(self) -> tuple[bool, str]:
        """Recompute the whole chain. This is what verify.py exposes."""
        prev = GENESIS
        expect_seq = 0
        for rec in self.read():
            if rec.seq != expect_seq:
                return False, f"seq gap at {rec.seq}, expected {expect_seq}"
            if rec.prev_hash != prev:
                return False, f"broken link at seq {rec.seq}"
            if link_hash(prev, rec.payload) != rec.hash:
                return False, f"payload tampered at seq {rec.seq}"
            prev = rec.hash
            expect_seq += 1
        return True, f"chain intact, {expect_seq} records"

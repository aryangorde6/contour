"""Record a live session; replay it with no credentials at all.

Every measurement the agent makes flows through `DataSource`, so recording is
a tee and replaying is a swap -- neither needs the pipeline to change shape.

The point is falsifiability. A judge with no Alpaca keys can run the exact
code that produced our published numbers against the exact quotes it saw, and
get the same decisions back. Replay is therefore *deterministic on purpose*:
it forces dry mode and a degraded brain, because an LLM in the loop would make
the same fixture produce different answers on different days.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .models import Bar, Leg

FORMAT = 1


class ReplayError(RuntimeError):
    """The fixture cannot answer a question the agent asked."""


def _leg_to_dict(l: Leg) -> dict[str, Any]:
    return {
        "symbol": l.symbol, "option_type": l.option_type, "strike": l.strike,
        "expiration_date": l.expiration_date.isoformat(),
        "bid": l.bid, "ask": l.ask, "delta": l.delta,
        "implied_volatility": l.implied_volatility,
        "open_interest": l.open_interest, "tradable": l.tradable,
        "close_price": l.close_price, "quote_age_s": l.quote_age_s,
    }


def _leg_from_dict(d: dict[str, Any]) -> Leg:
    return Leg(
        symbol=d["symbol"], side="buy", ratio_qty=1,
        option_type=d["option_type"], strike=d["strike"],
        expiration_date=date.fromisoformat(d["expiration_date"]),
        bid=d["bid"], ask=d["ask"], delta=d["delta"],
        implied_volatility=d["implied_volatility"],
        open_interest=d["open_interest"], tradable=d["tradable"],
        close_price=d["close_price"], quote_age_s=d["quote_age_s"],
    )


class Recorder:
    """Wraps a DataSource and tees every answer into a fixture.

    It delegates rather than reimplements, so a recorded run and a live run
    take an identical path through the agent -- if they did not, the fixture
    would prove nothing about the live code.
    """

    def __init__(self, inner: Any, path: str | Path):
        self.inner, self.path = inner, Path(path)
        self.data: dict[str, Any] = {
            "format": FORMAT,
            "captured_utc": datetime.now(timezone.utc).isoformat(),
            "spot": {}, "closes": {}, "legs": {}, "bars": {},
        }

    def spot(self, underlying: str) -> float:
        v = self.inner.spot(underlying)
        self.data["spot"][underlying] = v
        return v

    def closes(self, underlying: str, n: int = 11) -> list[float]:
        v = self.inner.closes(underlying, n)
        self.data["closes"][f"{underlying}|{n}"] = list(v)
        return v

    def legs(self, underlying: str, expiry: date, spot: float) -> list[Leg]:
        v = self.inner.legs(underlying, expiry, spot)
        self.data["legs"][f"{underlying}|{expiry.isoformat()}"] = [
            _leg_to_dict(l) for l in v]
        return v

    def bars(self, underlying: str, n: int) -> list[Bar]:
        v = self.inner.bars(underlying, n)
        self.data["bars"][f"{underlying}|{n}"] = [
            {"high": b.high, "low": b.low, "close": b.close, "volume": b.volume}
            for b in v]
        return v

    def save(self, now_et: datetime) -> Path:
        # The capture time is part of the fixture, not an afterthought: replay
        # restores it as "now" so quote ages and the session phase resolve the
        # way they did live. Without it G5 would veto every leg as stale.
        self.data["as_of_et"] = now_et.isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1, default=str) + "\n")
        return self.path


class Replay:
    """Serves a recorded fixture. No network, no credentials, no clock."""

    def __init__(self, data: dict[str, Any], path: Path | None = None):
        if data.get("format") != FORMAT:
            raise ReplayError(f"fixture format {data.get('format')!r}, "
                              f"expected {FORMAT}")
        self.data, self.path = data, path

    @classmethod
    def load(cls, path: str | Path) -> "Replay":
        p = Path(path)
        if not p.exists():
            raise ReplayError(f"no fixture at {p}")
        return cls(json.loads(p.read_text()), p)

    @classmethod
    def newest(cls, root: str | Path = "fixtures") -> "Replay":
        """The most recently RECORDED fixture, by its own captured timestamp.

        This used to take the lexically last filename, which is not the same
        thing and quietly served the wrong one: `2026-08-31-1305et.json` sorts
        before `2026-08-31-preopen.json`, so the mid-session recording lost to
        a pre-open one taken four hours earlier -- and `--replay`, the demo
        that is supposed to show twelve passing gates, showed a stale-quote
        veto instead. A fixture carries the time it was taken; sort on that
        and the filename can be anything.
        """
        found = list(Path(root).glob("*.json"))
        if not found:
            raise ReplayError(
                f"no fixtures in {root}/ -- record one with "
                f"`python -m contour --record {root}/NAME.json --dev`")

        def captured(path: Path) -> tuple[str, str]:
            try:
                stamp = json.loads(path.read_text()).get("captured_utc") or ""
            except Exception:                                    # noqa: BLE001
                stamp = ""                                       # unreadable -> oldest
            return (stamp, path.name)

        return cls.load(max(found, key=captured))

    @property
    def as_of_et(self) -> datetime:
        return datetime.fromisoformat(self.data["as_of_et"])

    def _get(self, bucket: str, key: str) -> Any:
        try:
            return self.data[bucket][key]
        except KeyError:
            raise ReplayError(
                f"fixture has no {bucket} for {key!r}; it was recorded over "
                f"{sorted(self.data.get(bucket, {}))}") from None

    def spot(self, underlying: str) -> float:
        return float(self._get("spot", underlying))

    def closes(self, underlying: str, n: int = 11) -> list[float]:
        return [float(c) for c in self._get("closes", f"{underlying}|{n}")]

    def legs(self, underlying: str, expiry: date, spot: float) -> list[Leg]:
        return [_leg_from_dict(d)
                for d in self._get("legs", f"{underlying}|{expiry.isoformat()}")]

    def bars(self, underlying: str, n: int) -> list[Bar]:
        """Absent in every fixture recorded before the profile existed, and
        that is not an error: those replays simply run without the filter and
        reproduce exactly the decisions they were recorded making. Only a
        MISSING BUCKET is forgiven -- a fixture that has bars but not for this
        name is a real gap and raises like any other."""
        bucket = self.data.get("bars")
        if not bucket:
            return []
        return [Bar(high=float(d["high"]), low=float(d["low"]),
                    close=float(d["close"]), volume=float(d["volume"]))
                for d in self._get("bars", f"{underlying}|{n}")]


class ReplayBroker:
    """Just enough broker to size a position. It cannot place an order.

    Replay always runs dry, so this is never asked to; making it structurally
    incapable is better than trusting a flag.
    """

    def __init__(self, nav: float = 100_000.0):
        self.nav = nav

    def account(self) -> dict[str, Any]:
        return {"equity": self.nav, "account_number": "REPLAY"}

"""Plain data the gates reason over. No behaviour, no I/O."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

Structure = Literal["NO_TRADE", "PUT_CS", "CALL_CS", "CONDOR"]


@dataclass(frozen=True)
class Bar:
    """One OHLCV bar. Volume is what `profile.py` needs and `closes()` drops."""
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class Leg:
    symbol: str                     # OCC symbol
    side: Literal["buy", "sell"]
    ratio_qty: int
    option_type: Literal["call", "put"]
    strike: float
    expiration_date: date
    bid: float
    ask: float
    delta: float | None             # None = not served; G6 treats as hard veto
    implied_volatility: float | None
    open_interest: int
    tradable: bool
    close_price: float | None
    quote_age_s: float | None = None   # seconds between quote ts and decision ts

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    @property
    def is_short(self) -> bool:
        return self.side == "sell"


@dataclass(frozen=True)
class Candidate:
    underlying: str
    structure: Structure
    legs: tuple[Leg, ...]
    net_credit: float               # per contract, positive = credit received
    wing_width: float
    contracts: int
    max_loss_per_contract: float

    @property
    def total_max_loss(self) -> float:
        return self.max_loss_per_contract * self.contracts

    @property
    def net_delta(self) -> float | None:
        if any(l.delta is None for l in self.legs):
            return None
        return sum(
            (l.delta * l.ratio_qty * (-1 if l.is_short else 1)) for l in self.legs
        )


@dataclass(frozen=True)
class OpenPosition:
    underlying: str
    structure: Structure
    contracts: int
    max_loss_per_contract: float
    credit_received: float

    @property
    def defined_risk(self) -> float:
        return self.max_loss_per_contract * self.contracts


@dataclass(frozen=True)
class Blackout:
    start: datetime                 # ET-aware
    end: datetime
    reason: str


@dataclass
class Book:
    nav: float
    session_pnl: float              # realized + unrealized, this session
    positions: tuple[OpenPosition, ...] = ()

    @property
    def open_risk(self) -> float:
        return sum(p.defined_risk for p in self.positions)

    def count_for(self, underlying: str) -> int:
        return sum(1 for p in self.positions if p.underlying == underlying)


@dataclass
class Context:
    """Everything the gates need from the outside world, resolved upfront so
    the gate functions themselves stay pure and trivially testable."""
    now_et: datetime
    halt_file_present: bool = False
    blackouts: tuple[Blackout, ...] = ()
    opened_this_cycle: tuple[str, ...] = ()      # underlyings already opened
    llm_no_new_entries_after: datetime | None = None
    client_order_id: str | None = None
    seen_client_order_ids: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class Measurement:
    """What the surface looks like for one underlying, right now."""
    underlying: str
    spot: float
    atm_iv: float
    rv10: float
    vrp_ratio: float
    skew25: float
    skew_z: float

    def as_dict(self) -> dict:
        return {
            "underlying": self.underlying, "spot": round(self.spot, 2),
            "atm_iv": round(self.atm_iv, 4), "rv10": round(self.rv10, 4),
            "vrp_ratio": round(self.vrp_ratio, 3),
            "skew25": round(self.skew25, 3), "skew_z": round(self.skew_z, 3),
        }

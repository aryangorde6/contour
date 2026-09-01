"""The I/O seam. Every measurement the agent makes flows through DataSource,
so `--replay` against a recorded fixture is a swap rather than a refactor.

Two endpoints must be merged, and the split is not cosmetic: option SNAPSHOTS
carry greeks, implied_volatility and quotes but NO open_interest, while the
Trading API's contract objects carry open_interest, tradable and close_price.
G5 screens liquidity on the Trading API fields specifically, not on the
indicative feed.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Protocol, Sequence

from . import config as C
from .models import Bar, Leg


class DataSource(Protocol):
    def spot(self, underlying: str) -> float: ...
    def closes(self, underlying: str, n: int) -> list[float]: ...
    def legs(self, underlying: str, expiry: date, spot: float) -> list[Leg]: ...
    # OPTIONAL. Fixtures recorded before the volume profile existed cannot
    # answer it, so every caller reaches it through getattr and treats a
    # missing method exactly like an empty window: no profile, no veto.
    def bars(self, underlying: str, n: int) -> list[Bar]: ...


BAND_PCT = 0.12          # +/-12% strike band; unfiltered chain calls will 429


class AlpacaData:
    def __init__(self, api_key: str, secret_key: str):
        from alpaca.data.historical.option import OptionHistoricalDataClient
        from alpaca.data.historical.stock import StockHistoricalDataClient
        from alpaca.trading.client import TradingClient

        self._opt = OptionHistoricalDataClient(api_key, secret_key)
        self._stk = StockHistoricalDataClient(api_key, secret_key)
        self._trd = TradingClient(api_key, secret_key, paper=True)

    def spot(self, underlying: str) -> float:
        from alpaca.data.requests import StockLatestQuoteRequest
        q = self._stk.get_stock_latest_quote(
            StockLatestQuoteRequest(symbol_or_symbols=underlying))[underlying]
        if q.bid_price and q.ask_price:
            return (q.bid_price + q.ask_price) / 2.0
        return float(q.ask_price or q.bid_price)

    def closes(self, underlying: str, n: int = 11) -> list[float]:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        from datetime import timedelta
        req = StockBarsRequest(
            symbol_or_symbols=underlying, timeframe=TimeFrame.Day,
            start=datetime.now(timezone.utc) - timedelta(days=n * 3),
        )
        bars = self._stk.get_stock_bars(req).data[underlying]
        return [b.close for b in bars][-n:]

    def bars(self, underlying: str, n: int = C.PROFILE_LOOKBACK_D) -> list[Bar]:
        """Daily OHLCV. `closes` drops volume, and volume is the whole point."""
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        from datetime import timedelta
        req = StockBarsRequest(
            symbol_or_symbols=underlying, timeframe=TimeFrame.Day,
            start=datetime.now(timezone.utc) - timedelta(days=n * 3),
        )
        bs = self._stk.get_stock_bars(req).data[underlying]
        return [Bar(high=float(b.high), low=float(b.low), close=float(b.close),
                    volume=float(b.volume)) for b in bs][-n:]

    def _contracts(self, underlying: str, expiry: date,
                   lo: float, hi: float) -> dict[str, dict]:
        from alpaca.trading.enums import AssetStatus
        from alpaca.trading.requests import GetOptionContractsRequest
        out: dict[str, dict] = {}
        token = None
        while True:
            req = GetOptionContractsRequest(
                underlying_symbols=[underlying], status=AssetStatus.ACTIVE,
                expiration_date=expiry, strike_price_gte=str(lo),
                strike_price_lte=str(hi), limit=1000, page_token=token,
            )
            res = self._trd.get_option_contracts(req)
            for c in res.option_contracts:
                out[c.symbol] = {
                    "open_interest": int(float(c.open_interest or 0)),
                    "tradable": bool(c.tradable),
                    "close_price": (float(c.close_price)
                                    if c.close_price is not None else None),
                    "strike": float(c.strike_price),
                    "type": c.type.value if hasattr(c.type, "value") else str(c.type),
                    "expiration_date": c.expiration_date,
                }
            token = getattr(res, "next_page_token", None)
            if not token:
                return out

    def legs(self, underlying: str, expiry: date, spot: float) -> list[Leg]:
        from alpaca.data.requests import OptionChainRequest
        lo, hi = spot * (1 - BAND_PCT), spot * (1 + BAND_PCT)
        meta = self._contracts(underlying, expiry, lo, hi)
        chain = self._opt.get_option_chain(OptionChainRequest(
            underlying_symbol=underlying, expiration_date=expiry,
            strike_price_gte=str(lo), strike_price_lte=str(hi),
        ))
        now = datetime.now(timezone.utc)
        legs: list[Leg] = []
        for sym, snap in chain.items():
            m = meta.get(sym)
            q = getattr(snap, "latest_quote", None)
            if m is None or q is None:
                continue
            g = getattr(snap, "greeks", None)
            age = ((now - q.timestamp).total_seconds()
                   if getattr(q, "timestamp", None) else None)
            legs.append(Leg(
                symbol=sym, side="buy", ratio_qty=1,
                option_type=m["type"], strike=m["strike"],
                expiration_date=m["expiration_date"],
                bid=float(q.bid_price or 0), ask=float(q.ask_price or 0),
                # Never coerce a missing greek to zero -- G6 vetoes on null.
                delta=(g.delta if g else None),
                implied_volatility=getattr(snap, "implied_volatility", None),
                open_interest=m["open_interest"], tradable=m["tradable"],
                close_price=m["close_price"], quote_age_s=age,
            ))
        return legs

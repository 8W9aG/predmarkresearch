"""Kalshi public market data. No authentication and no order capability."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx

from ..http import ReadClient

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"


class KalshiData:
    def __init__(self, per_second: float = 10.0, client: ReadClient | None = None):
        self.client = client or ReadClient(BASE_URL, per_second=per_second)

    def _pages(self, path: str, key: str, params: dict[str, Any]) -> Iterator[dict[str, Any]]:
        cursor = ""
        while True:
            query = {**params, **({"cursor": cursor} if cursor else {})}
            data = self.client.get_json(path, query)
            yield from data.get(key) or []
            cursor = data.get("cursor") or ""
            if not cursor:
                return

    def series(self) -> list[dict[str, Any]]:
        return list(self._pages("/series", "series", {"limit": 2000}))

    def historical_cutoff(self) -> dict[str, Any]:
        return self.client.get_json("/historical/cutoff")

    def settled_markets(self, series_ticker: str) -> Iterator[dict[str, Any]]:
        """Settled markets from both the live and archived tables, without duplicates."""
        seen: set[str] = set()
        # The archive rejects series_ticker combined with mve_filter, so combos are dropped here.
        sources = (
            ("/markets", {"status": "settled", "mve_filter": "exclude"}),
            ("/historical/markets", {}),
        )
        for path, extra in sources:
            params = {"series_ticker": series_ticker, "limit": 1000, **extra}
            for market in self._pages(path, "markets", params):
                if market["ticker"] not in seen and not market.get("mve_collection_ticker"):
                    seen.add(market["ticker"])
                    yield market

    def open_markets(self, limit_pages: int | None = 5) -> list[dict[str, Any]]:
        """Open non-combo markets. `limit_pages=None` lists all of them."""
        out: list[dict[str, Any]] = []
        pages = self._pages(
            "/markets", "markets", {"status": "open", "limit": 1000, "mve_filter": "exclude"}
        )
        for market in pages:
            out.append(market)
            if limit_pages is not None and len(out) >= limit_pages * 1000:
                break
        return out

    def markets(self, tickers: list[str], batch: int = 100) -> dict[str, dict[str, Any]]:
        """Look markets up by ticker, 100 per request, falling back to the archive one by one."""
        out: dict[str, dict[str, Any]] = {}
        for start in range(0, len(tickers), batch):
            chunk = tickers[start : start + batch]
            params = {"tickers": ",".join(chunk), "limit": 1000}
            for market in self.client.get_json("/markets", params).get("markets") or []:
                out[market["ticker"]] = market
        for ticker in tickers:
            if ticker in out:
                continue
            try:
                market = self.client.get_json(f"/historical/markets/{ticker}").get("market")
            except httpx.HTTPStatusError:
                continue
            if market:
                out[ticker] = market
        return out

    def candlesticks(
        self,
        series_ticker: str,
        ticker: str,
        start_ts: int,
        end_ts: int,
        period_minutes: int = 60,
        historical: bool = False,
    ) -> list[dict[str, Any]]:
        path = (
            f"/historical/markets/{ticker}/candlesticks"
            if historical
            else f"/series/{series_ticker}/markets/{ticker}/candlesticks"
        )
        params = {"start_ts": start_ts, "end_ts": end_ts, "period_interval": period_minutes}
        return self.client.get_json(path, params).get("candlesticks") or []

    def market(self, ticker: str) -> dict[str, Any]:
        return self.client.get_json(f"/markets/{ticker}").get("market") or {}

    def orderbook(self, ticker: str, depth: int = 25) -> dict[str, Any]:
        return self.client.get_json(f"/markets/{ticker}/orderbook", {"depth": depth})

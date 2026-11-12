"""Polymarket public market data: Gamma metadata, CLOB books and history, Data API v2 trades."""

from __future__ import annotations

import json
from typing import Any

from ..http import ReadClient


class PolymarketData:
    """Read-only client for the public Polymarket Gamma, CLOB and Data APIs."""

    def __init__(self, per_second: float = 5.0):
        self.gamma = ReadClient("https://gamma-api.polymarket.com", per_second=per_second)
        self.clob = ReadClient("https://clob.polymarket.com", per_second=per_second)
        self.data = ReadClient("https://data-api.polymarket.com", per_second=per_second)

    def markets(self, limit: int = 100, offset: int = 0, **filters: Any) -> list[dict[str, Any]]:
        """Return one page of Gamma markets. Extra keyword arguments are sent as query filters."""
        return self.gamma.get_json("/markets", {"limit": limit, "offset": offset, **filters})

    def top_token_ids(self, n: int) -> list[tuple[str, str]]:
        """(YES-token id, question) for the most actively traded open markets."""
        markets = self.markets(
            limit=n, active="true", closed="false", order="volume24hr", ascending="false"
        )
        out = []
        for market in markets:
            tokens = json.loads(market.get("clobTokenIds") or "[]")
            if tokens:
                out.append((tokens[0], market.get("question") or ""))
        return out

    def book(self, token_id: str) -> dict[str, Any]:
        """Return the current CLOB order book for one outcome token."""
        return self.clob.get_json("/book", {"token_id": token_id})

    def price_history(self, token_id: str, interval: str = "max", fidelity: int = 60) -> list[dict]:
        """Return the CLOB price history for one token. `fidelity` is the bar size in minutes."""
        params = {"market": token_id, "interval": interval, "fidelity": fidelity}
        return self.clob.get_json("/prices-history", params).get("history") or []

    def trades(self, **params: Any) -> dict[str, Any]:
        """One page of /v2/trades. Resend the same filters with `cursor` to continue."""
        return self.data.get_json("/v2/trades", params)

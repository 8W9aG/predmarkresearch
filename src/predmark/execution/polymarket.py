"""Polymarket read-only venue, usable for market data and paper trading.

Polymarket's geoblock documentation lists the United States as close-only on the API, so this
system places no Polymarket orders. https://docs.polymarket.com/api-reference/geoblock
Adding live order placement needs a confirmed eligible jurisdiction and account first, and the
check in `eligibility()` must stay in the path. It must not be worked around.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

import httpx

from ..http import ReadClient
from .models import Book, OrderRequest, VenueDisabled

D = Decimal
CLOB_URL = "https://clob.polymarket.com"


def parse_book(token_id: str, payload: dict[str, Any], ts: float) -> Book:
    bids = sorted(((D(x["price"]), D(x["size"])) for x in payload.get("bids") or []), reverse=True)
    asks = sorted((D(x["price"]), D(x["size"])) for x in payload.get("asks") or [])
    return Book(token_id, tuple(bids), tuple(asks), ts)


def eligibility() -> dict[str, Any]:
    resp = httpx.get("https://polymarket.com/api/geoblock", timeout=10)
    resp.raise_for_status()
    return resp.json()


class PolymarketBooks:
    """Book source for PaperVenue. Tickers are CLOB token ids."""

    name = "polymarket"

    def __init__(self, transport: httpx.BaseTransport | None = None):
        self.client = ReadClient(CLOB_URL, per_second=5, transport=transport)

    def book(self, token_id: str) -> Book:
        ts = time.time()
        return parse_book(token_id, self.client.get_json("/book", {"token_id": token_id}), ts)

    def place(self, req: OrderRequest):
        raise VenueDisabled(
            "Polymarket order placement is not implemented: the US is close-only on the API."
        )

"""The contract every venue adapter implements."""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from .models import Book, Fill, OrderRequest, PlaceResult, VenueOrder


class Venue(Protocol):
    name: str

    def book(self, ticker: str) -> Book: ...

    def place(self, req: OrderRequest) -> PlaceResult:
        """Raises VenueRejected if refused, VenueUnknown if the outcome cannot be determined."""
        ...

    def cancel(self, order_id: str, ticker: str) -> None:
        """Raises VenueRejected if the order is already gone."""
        ...

    def open_orders(self) -> list[VenueOrder]: ...

    def find_order(self, client_order_id: str, since_ts: float) -> VenueOrder | None:
        """Find an order in any state. Used to resolve UNKNOWN sends."""
        ...

    def positions(self) -> dict[str, Decimal]: ...

    def fills(self, since_ts: float) -> list[Fill]: ...

    def balance(self) -> Decimal: ...

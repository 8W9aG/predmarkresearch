"""The contract every venue adapter implements."""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from .models import Book, Fill, OrderRequest, PlaceResult, VenueOrder


class Venue(Protocol):
    """Interface that every venue adapter implements."""

    name: str
    # Which book of positions and orders this venue sees, e.g. "paper" or "kalshi-demo/2".
    # A journal is bound to the first account it trades on.
    account: str

    def book(self, ticker: str) -> Book:
        """Return the current order book for a ticker."""
        ...

    def place(self, req: OrderRequest) -> PlaceResult:
        """Raises VenueRejected if refused, VenueUnknown if the outcome cannot be determined."""
        ...

    def cancel(self, order_id: str, ticker: str) -> None:
        """Raises VenueRejected if the order is already gone."""
        ...

    def open_orders(self) -> list[VenueOrder]:
        """Return the orders resting at the venue."""
        ...

    def find_order(self, client_order_id: str, since_ts: float) -> VenueOrder | None:
        """Find an order in any state. Used to resolve UNKNOWN sends."""
        ...

    def positions(self) -> dict[str, Decimal]:
        """Return the net YES contracts held per ticker."""
        ...

    def fills(self, since_ts: float) -> list[Fill]:
        """Return fills at or after `since_ts`, in Unix seconds."""
        ...

    def balance(self) -> Decimal:
        """Return the cash available to trade, in dollars."""
        ...

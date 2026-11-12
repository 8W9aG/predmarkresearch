"""Venue-neutral order types. Prices and sizes are Decimals, quoted on the YES leg."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum


class Side(StrEnum):
    """Side of an order on the YES leg. The values are Kalshi's book sides."""

    BUY = "bid"  # buy YES, economically equal to selling NO
    SELL = "ask"  # sell YES, economically equal to buying NO at 1 - price


class TimeInForce(StrEnum):
    """How long an order stays working at the venue."""

    GTC = "good_till_canceled"
    IOC = "immediate_or_cancel"
    FOK = "fill_or_kill"


class OrderStatus(StrEnum):
    """Lifecycle state of an order."""

    PENDING_NEW = "pending_new"  # journaled, not yet acknowledged
    RESTING = "resting"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    UNKNOWN = "unknown"  # outcome of the send is not known; only reconcile may resolve it


TERMINAL = {OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED}


@dataclass(frozen=True)
class OrderRequest:
    """An order that a strategy asks the engine to send.

    `count` is in contracts and `price` is in dollars per YES contract. `client_order_id`
    lets `reconcile()` find an order whose send outcome is unknown.
    """

    ticker: str
    side: Side
    count: Decimal
    price: Decimal
    tif: TimeInForce = TimeInForce.GTC
    post_only: bool = False
    strategy: str = "manual"
    client_order_id: str = field(default_factory=lambda: uuid.uuid4().hex)


@dataclass(frozen=True)
class PlaceResult:
    """The venue's response to a placed order, in filled and remaining contracts."""

    order_id: str
    status: OrderStatus
    filled: Decimal = Decimal(0)
    remaining: Decimal = Decimal(0)


@dataclass(frozen=True)
class VenueOrder:
    """An order as the venue reports it."""

    order_id: str
    client_order_id: str | None
    ticker: str
    side: Side
    price: Decimal
    remaining: Decimal
    status: OrderStatus


@dataclass(frozen=True)
class Fill:
    """One execution reported by the venue.

    `fee` is in dollars and `ts` is in Unix seconds.
    """

    fill_id: str
    order_id: str
    ticker: str
    side: Side
    count: Decimal
    price: Decimal
    fee: Decimal
    is_taker: bool
    ts: float


@dataclass(frozen=True)
class Book:
    """bids are sorted best (highest) first; asks are sorted best (lowest) first."""

    ticker: str
    bids: tuple[tuple[Decimal, Decimal], ...]
    asks: tuple[tuple[Decimal, Decimal], ...]
    ts: float

    @property
    def best_bid(self) -> Decimal | None:
        """Highest bid price, or None if there are no bids."""
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> Decimal | None:
        """Lowest ask price, or None if there are no asks."""
        return self.asks[0][0] if self.asks else None


class VenueRejected(Exception):
    """The venue definitively did not accept the request."""


class VenueUnknown(Exception):
    """The request may or may not have reached the matching engine."""


class VenueDisabled(Exception):
    """This venue does not place orders from this system."""

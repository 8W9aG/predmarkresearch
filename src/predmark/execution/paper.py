"""Simulated venue that matches against real or replayed order books.

Known optimism, to be kept in mind when reading paper P&L:
- Taker orders walk displayed depth with no latency and no competition.
- Consecutive taker orders do not deplete the source book.
A resting order fills only when the opposite side strictly crosses its price, which is the
conservative treatment of unknown queue position. It cannot fill merely by being touched.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from .fees import KALSHI_DIRECT_INCREMENT, kalshi_fee
from .models import (
    Book,
    Fill,
    OrderRequest,
    OrderStatus,
    PlaceResult,
    Side,
    TimeInForce,
    VenueOrder,
    VenueRejected,
)
from .risk import Ledger

D = Decimal
FeeModel = Callable[[Decimal, Decimal, bool], Decimal]


def default_fee(count: Decimal, price: Decimal, is_taker: bool) -> Decimal:
    """A direct Kalshi account's fee: the $0.0001 grid, multiplier 1."""
    return kalshi_fee(count, price, is_taker, increment=KALSHI_DIRECT_INCREMENT)


@dataclass
class _Resting:
    req: OrderRequest
    order_id: str
    remaining: Decimal


class PaperVenue:
    name = "paper"
    account = "paper"

    def __init__(
        self,
        book_source: Callable[[str], Book],
        cash: Decimal = Decimal(1000),
        fee_model: FeeModel = default_fee,
        clock: Callable[[], float] | None = None,
    ):
        import time

        self.book_source = book_source
        self.cash = cash
        self.fee_model = fee_model
        self.clock = clock or time.time
        self._resting: dict[str, _Resting] = {}
        self._all: dict[str, VenueOrder] = {}
        self._fills: list[Fill] = []
        self._pos: dict[str, Decimal] = {}

    def settle(self, ticker: str, yes_value: Decimal) -> None:
        q = self._pos.pop(ticker, D(0))
        self.cash += q * yes_value

    def restore(self, ledger: Ledger, starting_cash: Decimal) -> None:
        """Rebuild state from an engine ledger, so a new process agrees with the journal."""
        open_cash = sum((p.cash for p in ledger.markets.values()), D(0))
        self.cash = starting_cash + ledger.realized - open_cash
        self._pos = {t: p.contracts for t, p in ledger.markets.items() if p.contracts != 0}

    def book(self, ticker: str) -> Book:
        book = self.book_source(ticker)
        self._sweep(book)
        return book

    def balance(self) -> Decimal:
        """Cash net of collateral: a short YES position locks $1 per contract."""
        return self.cash - sum((-q for q in self._pos.values() if q < 0), D(0))

    def _execute(
        self, req: OrderRequest, order_id: str, count: Decimal, price: Decimal, taker: bool
    ) -> None:
        fee = self.fee_model(count, price, taker)
        sign = 1 if req.side is Side.BUY else -1
        self.cash -= sign * count * price + fee
        self._pos[req.ticker] = self._pos.get(req.ticker, D(0)) + sign * count
        self._fills.append(
            Fill(
                uuid.uuid4().hex,
                order_id,
                req.ticker,
                req.side,
                count,
                price,
                fee,
                taker,
                self.clock(),
            )
        )

    def _required_cash(self, req: OrderRequest) -> Decimal:
        """Upper bound on the balance this order can consume if it fills at its limit."""
        fee = self.fee_model(req.count, req.price, True)
        if req.side is Side.BUY:
            q = self._pos.get(req.ticker, D(0))
            released = min(req.count, -q) if q < 0 else D(0)
            return req.count * req.price + fee - released
        q = self._pos.get(req.ticker, D(0))
        closing = min(req.count, q) if q > 0 else D(0)
        opening = req.count - closing
        return opening * (1 - req.price) - closing * req.price + fee

    def place(self, req: OrderRequest) -> PlaceResult:
        book = self.book(req.ticker)
        if self._required_cash(req) > self.balance():
            raise VenueRejected("insufficient balance")
        levels = book.asks if req.side is Side.BUY else book.bids
        crosses = [
            (p, q)
            for p, q in levels
            if (p <= req.price if req.side is Side.BUY else p >= req.price)
        ]
        if req.post_only and crosses:
            raise VenueRejected("post_only order would cross")
        available = sum((q for _, q in crosses), D(0))
        if req.tif is TimeInForce.FOK and available < req.count:
            raise VenueRejected("fill_or_kill not fully fillable")

        order_id = uuid.uuid4().hex
        remaining = req.count
        for price, qty in crosses:
            if remaining <= 0:
                break
            take = min(qty, remaining)
            self._execute(req, order_id, take, price, True)
            remaining -= take

        if remaining > 0 and req.tif is TimeInForce.GTC:
            self._resting[order_id] = _Resting(req, order_id, remaining)
            status = OrderStatus.RESTING
        elif remaining > 0 and remaining == req.count:
            status = OrderStatus.CANCELED
        elif remaining > 0:
            status = OrderStatus.CANCELED  # partial IOC: filled part stands, the rest is dropped
        else:
            status = OrderStatus.FILLED
        self._all[req.client_order_id] = VenueOrder(
            order_id, req.client_order_id, req.ticker, req.side, req.price, remaining, status
        )
        return PlaceResult(order_id, status, req.count - remaining, remaining)

    def _sweep(self, book: Book) -> None:
        for order_id, r in list(self._resting.items()):
            if r.req.ticker != book.ticker:
                continue
            levels = book.asks if r.req.side is Side.BUY else book.bids
            for price, qty in levels:
                crossed = price < r.req.price if r.req.side is Side.BUY else price > r.req.price
                if not crossed or r.remaining <= 0:
                    break
                take = min(qty, r.remaining)
                self._execute(r.req, order_id, take, r.req.price, False)
                r.remaining -= take
            if r.remaining <= 0:
                del self._resting[order_id]
                self._update(r, OrderStatus.FILLED)
            else:
                self._update(r, OrderStatus.RESTING)

    def _update(self, r: _Resting, status: OrderStatus) -> None:
        self._all[r.req.client_order_id] = VenueOrder(
            r.order_id,
            r.req.client_order_id,
            r.req.ticker,
            r.req.side,
            r.req.price,
            r.remaining,
            status,
        )

    def cancel(self, order_id: str, ticker: str) -> None:
        r = self._resting.pop(order_id, None)
        if r is None:
            raise VenueRejected("order not open")
        self._update(r, OrderStatus.CANCELED)

    def open_orders(self) -> list[VenueOrder]:
        return [
            VenueOrder(
                r.order_id,
                r.req.client_order_id,
                r.req.ticker,
                r.req.side,
                r.req.price,
                r.remaining,
                OrderStatus.RESTING,
            )
            for r in self._resting.values()
        ]

    def find_order(self, client_order_id: str, since_ts: float) -> VenueOrder | None:
        return self._all.get(client_order_id)

    def positions(self) -> dict[str, Decimal]:
        return dict(self._pos)

    def fills(self, since_ts: float) -> list[Fill]:
        """Fills are appended in time order, so scan back only as far as since_ts."""
        out: list[Fill] = []
        for f in reversed(self._fills):
            if f.ts < since_ts:
                break
            out.append(f)
        return out[::-1]

"""Pre-trade limits and the position ledger they are evaluated against."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal

from .models import Book, Fill, OrderRequest, Side

D = Decimal


@dataclass
class MarketPosition:
    """`cash` is net dollars paid, fees included. Outcome P&L is q - cash on YES, -cash on NO."""

    contracts: Decimal = D(0)
    cash: Decimal = D(0)
    fees: Decimal = D(0)


@dataclass
class Ledger:
    """Positions per market and realized P&L, built from fills and settlements."""

    markets: dict[str, MarketPosition] = field(default_factory=dict)
    realized: Decimal = D(0)

    def apply(self, fill: Fill) -> None:
        """Add a fill to its market's contracts, cash, and fees."""
        pos = self.markets.setdefault(fill.ticker, MarketPosition())
        sign = 1 if fill.side is Side.BUY else -1
        pos.contracts += sign * fill.count
        pos.cash += sign * fill.count * fill.price + fill.fee
        pos.fees += fill.fee

    def settle(self, ticker: str, yes_value: Decimal) -> None:
        """Close a market at `yes_value` dollars per YES contract and realize its P&L."""
        pos = self.markets.pop(ticker, None)
        if pos is not None:
            self.realized += pos.contracts * yes_value - pos.cash

    def contracts(self, ticker: str) -> Decimal:
        """Return the net YES contracts held in a ticker, negative when short."""
        pos = self.markets.get(ticker)
        return pos.contracts if pos else D(0)

    def nav(self, books: dict[str, Book]) -> Decimal:
        """Liquidation value plus realized P&L: longs at the bid, shorts at the ask."""
        total = self.realized
        for ticker, pos in self.markets.items():
            book = books.get(ticker)
            if pos.contracts > 0:
                mark = (book.best_bid if book else None) or D(0)
            elif pos.contracts < 0:
                ask = book.best_ask if book else None
                mark = D(1) if ask is None else ask
            else:
                mark = D(0)
            total += pos.contracts * mark - pos.cash
        return total


def worst_case_loss(
    pos: MarketPosition | None, open_orders: Iterable[tuple[Side, Decimal, Decimal]] = ()
) -> Decimal:
    """Largest loss across outcomes if every buy, or every sell, rests and fills. Never negative."""
    q = pos.contracts if pos else D(0)
    cash = pos.cash if pos else D(0)
    buys = [(c, p) for s, c, p in open_orders if s is Side.BUY]
    sells = [(c, p) for s, c, p in open_orders if s is Side.SELL]
    worst = max(cash - q, cash, D(0))  # NO outcome loses cash; YES outcome loses cash - q
    if buys:
        c_add = sum((c * p for c, p in buys), D(0))
        q_add = sum((c for c, _ in buys), D(0))
        worst = max(worst, cash + c_add - (q + q_add), cash + c_add)
    if sells:
        c_sub = sum((c * p for c, p in sells), D(0))
        q_sub = sum((c for c, _ in sells), D(0))
        worst = max(worst, cash - c_sub - (q - q_sub), cash - c_sub)
    return worst


@dataclass(frozen=True)
class RiskLimits:
    """Deliberately small defaults. Raising them is a decision for the account owner.

    `max_total_loss=None` skips the account-wide check, whose cost grows with the number of
    positions. Only use it where something else caps the account, such as paper cash.
    """

    max_order_contracts: Decimal = D(100)
    max_order_notional: Decimal = D(25)
    max_position_contracts: Decimal = D(200)
    max_market_loss: Decimal = D(50)
    max_total_loss: Decimal | None = D(200)
    max_daily_loss: Decimal = D(50)
    max_open_orders: int = 20
    max_orders_per_minute: int = 30
    price_collar: Decimal = D("0.05")
    max_book_age_seconds: float = 5.0
    allowed_prefixes: tuple[str, ...] = ()


def check_order(
    req: OrderRequest,
    limits: RiskLimits,
    ledger: Ledger,
    open_orders: dict[str, list[tuple[Side, Decimal, Decimal]]],
    book: Book,
    now: float,
    orders_last_minute: int,
) -> str | None:
    """Return the reason to reject, or None if the order passes every limit."""
    if not (D(0) < req.price < D(1)):
        return f"price {req.price} outside (0, 1)"
    if req.count <= 0:
        return f"count {req.count} must be positive"
    if limits.allowed_prefixes and not req.ticker.startswith(limits.allowed_prefixes):
        return f"{req.ticker} not in allowed prefixes"
    if req.count > limits.max_order_contracts:
        return f"count {req.count} above {limits.max_order_contracts}"
    # A sell's dollars at risk are (1 - price) per contract.
    unit_risk = req.price if req.side is Side.BUY else 1 - req.price
    if req.count * unit_risk > limits.max_order_notional:
        return f"notional {req.count * unit_risk} above {limits.max_order_notional}"
    if orders_last_minute >= limits.max_orders_per_minute:
        return "order rate limit reached"
    if sum(len(v) for v in open_orders.values()) >= limits.max_open_orders:
        return "open order limit reached"
    if now - book.ts > limits.max_book_age_seconds:
        return f"book is {now - book.ts:.1f}s old"
    if (
        req.side is Side.BUY
        and book.best_ask is not None
        and req.price > book.best_ask + limits.price_collar
    ):
        return f"buy at {req.price} is beyond the collar from ask {book.best_ask}"
    if (
        req.side is Side.SELL
        and book.best_bid is not None
        and req.price < book.best_bid - limits.price_collar
    ):
        return f"sell at {req.price} is beyond the collar from bid {book.best_bid}"

    resting = open_orders.get(req.ticker, [])
    same = sum((c for s, c, _ in resting if s is req.side), D(0))
    sign = 1 if req.side is Side.BUY else -1
    projected = ledger.contracts(req.ticker) + sign * (same + req.count)
    if abs(projected) > limits.max_position_contracts:
        return f"projected position {projected} above {limits.max_position_contracts}"

    after = [*resting, (req.side, req.count, req.price)]
    market_loss = worst_case_loss(ledger.markets.get(req.ticker), after)
    if market_loss > limits.max_market_loss:
        return f"market worst-case loss {market_loss} above {limits.max_market_loss}"
    if limits.max_total_loss is None:
        return None
    total = market_loss
    for ticker in set(ledger.markets) | set(open_orders):
        if ticker != req.ticker:
            total += worst_case_loss(ledger.markets.get(ticker), open_orders.get(ticker, []))
    if total > limits.max_total_loss:
        return f"total worst-case loss {total} above {limits.max_total_loss}"
    return None

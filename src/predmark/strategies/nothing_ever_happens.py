"""Nothing Ever Happens: buy NO on popular markets and hold to settlement. Paper or Kalshi demo.

Buying NO at 1 - p is selling YES at p, so each entry is an immediate-or-cancel sell at the bid.
Popular means 24-hour volume of at least MIN_VOLUME_24H on the live listing, which is known when
the pass runs. The backtest applies the same threshold to trailing candle volume. TOP caps how many
markets one pass considers, to keep compute small.

Paper fills and the backtest charge a direct Kalshi account's taker fee: multiplier 1, rounded up
to $0.0001 (see `execution/fees.py`). The backtest uses the same one-contract lot by default.
On Kalshi, markets on an exchange index without cash are skipped and counted as `unfunded`.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..execution.engine import Engine
from ..execution.models import OrderRequest, OrderStatus, Side, TimeInForce
from .base import (
    PAPER_CASH,
    Lookup,
    Strategy,
    funded,
    open_engine,
    record_run,
    report,
    settle_positions,
)

NAME = "nothing_ever_happens"  # the strategy tag on journaled orders
STRATEGY_NAME = "nothing-ever-happens"
D = Decimal
MIN_VOLUME_24H = 10_000
TOP = 1000


@dataclass
class PassSummary:
    """Counts of what one pass did with each market, and why it stopped early, if it did."""

    listed: int = 0
    entered: int = 0
    partial: int = 0  # IOC filled in part: displayed size below one contract
    no_fill: int = 0
    no_bid: int = 0
    skipped: int = 0
    unfunded: int = 0  # on an exchange index where the account holds no cash
    settled: int = 0
    stopped: str | None = None
    unresolved: list[str] = field(default_factory=list)


def _order_key(market: dict[str, Any]) -> str:
    """A stable order that is unrelated to price, volume, or topic."""
    return hashlib.sha256(market["ticker"].encode()).hexdigest()


def _listed_without_bid(market: dict[str, Any]) -> bool:
    """The listing's quote, used only to skip fetching books that have no bid."""
    quoted = market.get("yes_bid_dollars")
    return quoted is not None and D(str(quoted)) <= 0


def volume_24h(market: dict[str, Any]) -> float:
    """Return a listing's 24-hour volume, or 0 when it is missing."""
    return float(market.get("volume_24h_fp") or 0)


def popular(
    markets: Iterable[dict[str, Any]], min_volume: float = MIN_VOLUME_24H, top: int | None = TOP
) -> list[dict[str, Any]]:
    """Markets at or above the volume threshold, busiest first, at most `top` of them."""
    chosen = sorted(
        (m for m in markets if volume_24h(m) >= min_volume),
        key=lambda m: (-volume_24h(m), _order_key(m)),
    )
    return chosen if top is None else chosen[:top]


def run_pass(
    engine: Engine,
    markets: Iterable[dict[str, Any]],
    lookup: Lookup,
    count: Decimal = D(1),
    max_new: int | None = None,
    progress: Callable[[int, PassSummary], None] | None = None,
    tradable: Callable[[dict[str, Any]], bool] = lambda market: True,
) -> PassSummary:
    """Settle what has resolved, then enter every market not yet attempted, in hash order."""
    summary = PassSummary()
    summary.settled, summary.unresolved = settle_positions(engine, lookup)
    attempted = {
        r["ticker"]
        for r in engine.db.execute(
            "SELECT ticker FROM orders WHERE strategy = ? AND status != ?",
            (NAME, OrderStatus.REJECTED.value),
        )
    }
    ordered = sorted(markets, key=_order_key)
    summary.listed = len(ordered)
    submitted = 0
    for i, market in enumerate(ordered):
        if progress and i and i % 5000 == 0:
            progress(i, summary)
        if max_new is not None and submitted >= max_new:
            break
        ticker = market["ticker"]
        if ticker in attempted:
            summary.skipped += 1
            continue
        if not tradable(market):
            summary.unfunded += 1
            continue
        if _listed_without_bid(market):
            summary.no_bid += 1
            continue
        bid = engine.venue.book(ticker).best_bid
        if bid is None:
            summary.no_bid += 1
            continue
        status = engine.submit(
            OrderRequest(ticker, Side.SELL, count, bid, tif=TimeInForce.IOC, strategy=NAME)
        )
        submitted += 1
        if status is OrderStatus.FILLED:
            summary.entered += 1
        elif status is OrderStatus.CANCELED and engine.ledger.contracts(ticker) != 0:
            summary.partial += 1
        elif status is OrderStatus.CANCELED:
            summary.no_fill += 1
        else:
            row = engine.db.execute(
                "SELECT reason FROM orders ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            summary.stopped = f"{status.value}: {row['reason']}"
            break
    return summary


def run(
    journal: str | Path,
    kill_file: str | Path,
    log: Callable[[str], None] = print,
    cash: Decimal | str = PAPER_CASH,
    count: Decimal | str = D(1),
    max_new: int | None = None,
    min_volume: float = MIN_VOLUME_24H,
    top: int | None = TOP,
    data: Any = None,
    venue: str = "paper",
) -> dict[str, Any]:
    """One pass over popular markets, appended to `<journal>.runs.jsonl`.

    On `kalshi-demo` the default `RiskLimits` apply, so a pass stops at the first limit it reaches
    and reports why in `pass.stopped`. `cash` applies to paper only.
    """
    engine, data = open_engine(journal, kill_file, venue, cash, data, log, STRATEGY_NAME)
    started = time.time()
    markets = data.open_markets(limit_pages=None)
    chosen = popular(markets, min_volume, top)
    log(
        f"listed {len(markets)} open markets, {len(chosen)} chosen with 24h volume >= "
        f"{min_volume} (cap {top}), holding {len(engine.ledger.markets)}"
    )

    def progress(i: int, s: PassSummary) -> None:
        log(f"{i}/{len(chosen)} entered={s.entered} no_bid={s.no_bid} skipped={s.skipped}")

    summary = run_pass(
        engine, chosen, data.markets, D(str(count)), max_new, progress, funded(engine)
    )
    result = {
        "ts": started,
        "seconds": round(time.time() - started),
        "selection": {"min_volume_24h": min_volume, "top": top, "chosen": len(chosen)},
        "pass": asdict(summary),
        "account": report(engine, markets),
        "venue": venue,
        "halted": engine.halt_reason,
        # The key keeps its name on paper so existing run logs and the website still read it.
        ("paper_cash" if venue == "paper" else "balance"): str(engine.venue.balance()),
    }
    record_run(journal, result)
    return result


def backtest(
    panel: str | Path, window_hours: int = 24, lot: int = 1, min_volume: float = MIN_VOLUME_24H
) -> dict[str, Any]:
    """Run the backtest in `predmark.backtest.nothing_ever_happens` on a panel's `raw.jsonl`."""
    from ..backtest import nothing_ever_happens as bt

    return bt.run(panel, window_hours, lot, min_volume)


STRATEGY = Strategy(
    name=STRATEGY_NAME,
    description="buy NO at the bid on popular markets and hold to settlement",
    run=run,
    backtest=backtest,
    evidence=("research/experiments/E002-nothing-ever-happens",),
)

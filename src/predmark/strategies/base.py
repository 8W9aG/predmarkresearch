"""What every strategy provides, and the paper-trading pieces they share.

A strategy is registered in `predmark.strategies.REGISTRY` with:
- `run(journal, kill_file, log, venue, **options)`: one pass on `venue` (`paper` or `kalshi-demo`).
  Returns a JSON-able summary.
- `backtest(panel, **options)`: evaluation on a panel from `predmark.backtest.panel`, or None.
- `evidence`: experiment directories whose `report.md` the website links to.

Strategies reach venues only through `Engine`, which `open_engine` builds for paper or Kalshi demo.
The runner in `predmark.strategies.runner` gives each strategy its own journal per venue and runs
them all on a schedule.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..execution.engine import Engine
from ..execution.models import Book
from ..execution.risk import RiskLimits, worst_case_loss

D = Decimal
Lookup = Callable[[list[str]], dict[str, dict[str, Any]]]

# Paper only, approved by the account owner on 2026-11-08 so a strategy can hold every market.
# Paper cash is the account-wide cap, so the total worst-case check is skipped.
PAPER_LIMITS = RiskLimits(
    max_total_loss=None,
    max_daily_loss=D(10**9),
    max_orders_per_minute=10**6,
)
PAPER_CASH = D(1_000_000)


@dataclass(frozen=True)
class Strategy:
    """Registry entry for one strategy: its name, description, run and optional backtest."""

    name: str
    description: str
    run: Callable[..., dict[str, Any]]
    backtest: Callable[..., dict[str, Any]] | None = None
    # Experiment directories with a report.md about this strategy, shown on the website.
    evidence: tuple[str, ...] = ()


class CachedBooks:
    """Fetch each book once per order: the strategy, the risk check, and the fill all read it."""

    def __init__(self, fetch: Callable[[str], Book], ttl: float = 2.0, clock=time.time):
        self.fetch, self.ttl, self.clock = fetch, ttl, clock
        self._last: tuple[str, Book, float] | None = None

    def __call__(self, ticker: str) -> Book:
        """Return the cached book for `ticker` if under `ttl` seconds old, else fetch a new one."""
        now = self.clock()
        if self._last and self._last[0] == ticker and now - self._last[2] < self.ttl:
            return self._last[1]
        book = self.fetch(ticker)
        self._last = (ticker, book, now)
        return book


VENUES = ("paper", "kalshi-demo")


def open_engine(
    journal: str | Path,
    kill_file: str | Path,
    venue: str = "paper",
    cash: Decimal = PAPER_CASH,
    data=None,
    log: Callable[[str], None] = print,
    name: str | None = None,
):
    """An engine restored from its journal. Returns (engine, data).

    `paper` fills against live production books with `PAPER_LIMITS`. `kalshi-demo` sends real
    orders to Kalshi's demo exchange, with the default `RiskLimits` and demo market data, and
    reconciles the journal with the exchange first. On demo, `name` picks the journal's subaccount
    from `DEMO_SUBACCOUNTS`. A mismatch halts the engine, so the pass
    places no orders. Production is not an option here.
    """
    from ..data.kalshi import KalshiData
    from ..execution.kalshi import URLS, KalshiVenue, demo_subaccount, parse_book
    from ..execution.paper import PaperVenue
    from ..http import ReadClient

    if venue == "paper":
        data = data or KalshiData(per_second=10)
        books = CachedBooks(lambda t: parse_book(t, data.orderbook(t), time.time()))
        paper = PaperVenue(books, cash=D(str(cash)))
        engine = Engine(paper, journal, limits=PAPER_LIMITS, kill_file=kill_file)
        paper.restore(engine.ledger, D(str(cash)))
        return engine, data
    if venue == "kalshi-demo":
        data = data or KalshiData(client=ReadClient(URLS["demo"], per_second=10))
        if name is None:
            raise ValueError("kalshi-demo needs the strategy name to pick its subaccount")
        kalshi = KalshiVenue.from_env(env="demo", subaccount=demo_subaccount(name))
        engine = Engine(kalshi, journal, kill_file=kill_file)
        problems = engine.reconcile()
        if problems:
            log("reconcile found problems, engine halted: " + "; ".join(problems))
        return engine, data
    raise ValueError(f"unknown venue {venue!r}, expected one of {VENUES}")


def funded(engine: Engine) -> Callable[[dict[str, Any]], bool]:
    """Whether the venue holds cash on a market's exchange index (shard).

    Paper is one pool, so every market passes. Kalshi funds each shard separately and refuses
    orders on an unfunded one, so a strategy skips those markets instead of stopping there.
    """
    shard_balances = getattr(engine.venue, "shard_balances", None)
    if shard_balances is None:
        return lambda market: True
    cash = {i for i, b in shard_balances().items() if b > 0}
    return lambda market: int(market.get("exchange_index") or 0) in cash


def open_offline(journal: str | Path) -> Engine:
    """The journal's engine with no market access, for reading account state."""
    from ..execution.paper import PaperVenue

    def offline(ticker: str) -> Book:
        raise RuntimeError("offline engine has no books")

    return Engine(PaperVenue(offline), journal, bind_account=False)


def settle_positions(engine: Engine, lookup: Lookup) -> tuple[int, list[str]]:
    """Apply results for held markets that have resolved. Returns (settled, unresolved)."""
    held = list(engine.ledger.markets)
    found = lookup(held) if held else {}
    settled, unresolved = 0, []
    for ticker in held:
        market = found.get(ticker) or {}
        result = market.get("result")
        if result not in {"yes", "no"}:
            if market.get("status") in {"settled", "finalized"}:
                unresolved.append(ticker)
            continue
        value = D(1) if result == "yes" else D(0)
        engine.apply_settlement(ticker, value)
        settle = getattr(engine.venue, "settle", None)
        if settle:
            settle(ticker, value)
        settled += 1
    return settled, unresolved


def listing_book(market: dict[str, Any], ts: float) -> Book:
    """Top of book from a market listing, for marking positions without extra requests."""

    def level(price: str, size: str) -> tuple[tuple[Decimal, Decimal], ...]:
        p, q = D(str(market.get(price) or 0)), D(str(market.get(size) or 0))
        return ((p, q),) if 0 < p < 1 else ()

    return Book(
        market["ticker"],
        level("yes_bid_dollars", "yes_bid_size_fp"),
        level("yes_ask_dollars", "yes_ask_size_fp"),
        ts,
    )


def report(engine: Engine, markets: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Account state from the journal. With a listing, also a mark-to-market value.

    Held markets missing from the listing (closed, awaiting settlement) are marked at the worst
    case: a YES ask of 1 for short YES, a bid of 0 for long YES. So `mark_nav` is conservative.
    """
    ledger = engine.ledger
    settled = engine.db.execute("SELECT COUNT(*) AS n FROM settlements").fetchone()["n"]
    fees = sum((D(r["fee"]) for r in engine.db.execute("SELECT fee FROM fills")), D(0))
    out: dict[str, Any] = {
        "open_positions": len(ledger.markets),
        "settled_positions": settled,
        "realized_pnl": str(ledger.realized),
        "fees_paid": str(fees),
        "capital_at_risk": str(sum((worst_case_loss(p) for p in ledger.markets.values()), D(0))),
    }
    if markets is not None:
        now = time.time()
        listed = {m["ticker"]: m for m in markets}
        books = {t: listing_book(listed[t], now) for t in ledger.markets if t in listed}
        out["mark_nav"] = str(ledger.nav(books))
        out["held_unquoted"] = sum(t not in listed for t in ledger.markets)
    return out


def record_run(journal: str | Path, result: dict[str, Any]) -> None:
    """Append a pass summary to `<journal>.runs.jsonl`."""
    with Path(journal).with_suffix(".runs.jsonl").open("a") as fh:
        fh.write(json.dumps(result, sort_keys=True, default=str) + "\n")

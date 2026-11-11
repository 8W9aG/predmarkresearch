"""Run the full order lifecycle against Kalshi's demo exchange, through `Engine`.

    uv run python -m predmark.execution.demo_check [--ticker T]

Needs KALSHI_KEY_ID and KALSHI_PRIVATE_KEY_PATH for a demo account (demo.kalshi.co), exported in
the shell. The venue is always built with env="demo", and this module has no production option.
It trades in its own demo subaccount (`DEMO_SUBACCOUNTS["demo-check"]`), so strategy journals
on the same account never see its fills.

Steps, each one-contract and each followed by `reconcile()`:
- start: the journal and the exchange agree. A halt left by an earlier run is cleared only if so.
- rest: a post-only buy at 1 cent rests and appears in the exchange's open orders.
- cancel: the engine cancels it, and it leaves the open orders.
- fill: an immediate-or-cancel buy at the ask fills, and the journal's position matches.
- close: an immediate-or-cancel sell at the bid closes the position.
- crash: a resting order is sent but its response is lost. The engine halts and refuses new orders.
  A new engine on the same journal, as after a restart, finds the order with `reconcile()`, resumes,
  and cancels it.

The check stops at the first failed step and cancels any open order it left. It does not close a
position on failure, so a failed `fill` or `close` step may leave one contract for a person to
review. Results go to `data/demo-check/<UTC time>.json`.
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from .engine import Engine
from .models import OrderRequest, OrderStatus, PlaceResult, Side, TimeInForce, VenueUnknown
from .venue import Venue

D = Decimal
ONE = D(1)
FAR_BID = D("0.01")
OUT_DIR = Path("data/demo-check")
STRATEGY = "demo-check"


@dataclass
class Step:
    name: str
    ok: bool
    detail: str


class CheckFailed(Exception):
    pass


class LostResponse:
    """Sends each order to the real venue, then reports the send outcome as unknown."""

    def __init__(self, venue: Venue):
        self._venue = venue
        self.name = venue.name

    def __getattr__(self, attr: str) -> Any:
        return getattr(self._venue, attr)

    def place(self, req: OrderRequest) -> PlaceResult:
        self._venue.place(req)
        raise VenueUnknown("response lost on purpose by demo_check")


def pick_market(markets: list[dict[str, Any]], exchange_index: int = 0) -> str | None:
    """The open market with the tightest quoted spread, both sides between 5 and 95 cents.

    Only markets on `exchange_index`: Kalshi funds each exchange index (shard) separately, and an
    order on an unfunded one is refused with `insufficient_shard_balance`.
    """

    def quote(m: dict[str, Any], key: str) -> Decimal:
        return D(str(m.get(key) or 0))

    usable = [
        m
        for m in markets
        if m.get("exchange_index", 0) == exchange_index
        and D("0.05") <= quote(m, "yes_bid_dollars") < quote(m, "yes_ask_dollars") <= D("0.95")
        and quote(m, "yes_bid_size_fp") >= ONE
        and quote(m, "yes_ask_size_fp") >= ONE
    ]
    if not usable:
        return None
    best = min(
        usable,
        key=lambda m: (quote(m, "yes_ask_dollars") - quote(m, "yes_bid_dollars"), m["ticker"]),
    )
    return best["ticker"]


def run_lifecycle(
    venue: Venue,
    journal: str | Path,
    kill_file: str | Path,
    ticker: str,
    log: Callable[[str], None] = print,
    wait_seconds: float = 15.0,
    sleep: Callable[[float], None] = time.sleep,
) -> list[Step]:
    """Every step in order. Stops at the first failure, after cancelling open orders."""
    steps: list[Step] = []
    engine = Engine(venue, journal, kill_file=kill_file)

    def passed(name: str, detail: str) -> None:
        steps.append(Step(name, True, detail))
        log(f"PASS {name}: {detail}")

    def require(cond: bool, what: str) -> None:
        if not cond:
            raise CheckFailed(what)

    def clean(eng: Engine, when: str) -> None:
        problems = eng.reconcile()
        require(not problems, f"reconcile {when}: {problems}")

    def eventually(cond: Callable[[], bool], between: Callable[[], object] = lambda: None):
        """Kalshi's listings trail its matching engine, so venue state is polled, not read once."""
        deadline = time.monotonic() + wait_seconds
        while not cond() and time.monotonic() < deadline:
            sleep(0.5)
            between()
        return cond()

    def settle_position(eng: Engine, want: Decimal) -> None:
        eventually(lambda: eng.ledger.contracts(ticker) == want, eng.sync_fills)
        got = eng.ledger.contracts(ticker)
        require(got == want, f"journal position {got}, expected {want}")
        at_venue = lambda: venue.positions().get(ticker, D(0))  # noqa: E731
        eventually(lambda: at_venue() == want)
        require(at_venue() == want, f"venue position {at_venue()}, expected {want}")

    def submit(eng: Engine, side: Side, price: Decimal, tif: TimeInForce, post_only=False):
        req = OrderRequest(
            ticker, side, ONE, price, tif=tif, post_only=post_only, strategy=STRATEGY
        )
        return req.client_order_id, eng.submit(req)

    def reason(eng: Engine, cid: str) -> str:
        row = eng.db.execute(
            "SELECT reason FROM orders WHERE client_order_id = ?", (cid,)
        ).fetchone()
        return row["reason"] if row else "not journaled"

    def is_open(cid: str) -> bool:
        return any(o.client_order_id == cid for o in venue.open_orders())

    current = "start"
    try:
        problems = engine.reconcile()
        require(not problems, f"journal and venue disagree: {problems}")
        if engine.halt_reason:
            require(engine.resume(), f"could not clear halt: {engine.halt_reason}")
        passed("start", f"balance {venue.balance()}, position {engine.ledger.contracts(ticker)}")
        start_position = engine.ledger.contracts(ticker)

        current = "rest"
        book = venue.book(ticker)
        require(book.best_ask is not None and book.best_bid is not None, "book has no two sides")
        require(book.best_ask > FAR_BID, f"ask {book.best_ask} would cross a 1 cent bid")
        cid, status = submit(engine, Side.BUY, FAR_BID, TimeInForce.GTC, post_only=True)
        require(status is OrderStatus.RESTING, f"status {status}: {reason(engine, cid)}")
        require(eventually(lambda: is_open(cid)), "resting order missing from venue open orders")
        clean(engine, "with a resting order")
        passed("rest", f"post-only buy at {FAR_BID} rests")

        current = "cancel"
        require(engine.cancel(cid), "engine.cancel returned False")
        require(eventually(lambda: not is_open(cid)), "cancelled order still open at venue")
        clean(engine, "after cancel")
        passed("cancel", "order cancelled and gone from venue")

        current = "fill"
        ask = venue.book(ticker).best_ask
        require(ask is not None, "no ask to buy")
        cid, status = submit(engine, Side.BUY, ask, TimeInForce.IOC)
        require(status is OrderStatus.FILLED, f"status {status}: {reason(engine, cid)}")
        settle_position(engine, start_position + ONE)
        clean(engine, "after fill")
        passed("fill", f"bought 1 at {ask}")

        current = "close"
        bid = venue.book(ticker).best_bid
        require(bid is not None, "no bid to sell into")
        cid, status = submit(engine, Side.SELL, bid, TimeInForce.IOC)
        require(status is OrderStatus.FILLED, f"status {status}: {reason(engine, cid)}")
        settle_position(engine, start_position)
        clean(engine, "after close")
        passed("close", f"sold 1 at {bid}")

        current = "crash"
        engine.db.close()
        lossy = Engine(LostResponse(venue), journal, kill_file=kill_file)
        cid, status = submit(lossy, Side.BUY, FAR_BID, TimeInForce.GTC, post_only=True)
        require(status is OrderStatus.UNKNOWN, f"lost response gave {status}")
        require(lossy.halt_reason is not None, "engine did not halt on an unknown send")
        _, blocked = submit(lossy, Side.BUY, FAR_BID, TimeInForce.GTC, post_only=True)
        require(blocked is OrderStatus.REJECTED, f"halted engine accepted an order: {blocked}")
        lossy.db.close()
        engine = Engine(venue, journal, kill_file=kill_file)
        clean(engine, "after restart")
        row = engine.db.execute(
            "SELECT status FROM orders WHERE client_order_id = ?", (cid,)
        ).fetchone()
        require(row["status"] == OrderStatus.RESTING.value, f"resolved to {row['status']}")
        require(engine.resume(), f"resume refused: {engine.halt_reason}")
        require(engine.cancel(cid), "could not cancel the recovered order")
        clean(engine, "after cancelling the recovered order")
        passed("crash", "unknown send halted, reconcile found it after restart, then cancelled")
    except Exception as exc:  # noqa: BLE001 - every failure is reported, then orders are cleaned up
        steps.append(Step(current, False, f"{type(exc).__name__}: {exc}"))
        log(f"FAIL {current}: {exc}")
        try:
            log(f"cancelled {engine.cancel_all()} open order(s)")
        except Exception as cleanup:  # noqa: BLE001
            log(f"cleanup failed, check open orders by hand: {cleanup}")
    return steps


def main(argv: list[str] | None = None) -> int:
    from ..data.kalshi import KalshiData
    from ..http import ReadClient
    from .kalshi import URLS, KalshiVenue, demo_subaccount

    parser = argparse.ArgumentParser(prog="python -m predmark.execution.demo_check")
    parser.add_argument("--ticker", help="demo market to trade; default: tightest spread")
    parser.add_argument("--journal", default=str(OUT_DIR / "journal.sqlite"))
    parser.add_argument(
        "--subaccount", type=int, default=demo_subaccount(STRATEGY), help="demo subaccount"
    )
    args = parser.parse_args(argv)

    try:
        venue = KalshiVenue.from_env(env="demo", subaccount=args.subaccount)
    except KeyError as exc:
        raise SystemExit(f"set {exc.args[0]} for a Kalshi demo account (see .env.example)") from exc
    ticker = args.ticker
    if ticker is None:
        data = KalshiData(client=ReadClient(URLS["demo"], per_second=10))
        ticker = pick_market(data.open_markets(limit_pages=3))
        if ticker is None:
            raise SystemExit("no demo market has both sides quoted between 5 and 95 cents")
    print(f"demo market {ticker}, subaccount {args.subaccount}")

    journal = Path(args.journal)
    steps = run_lifecycle(venue, journal, journal.parent / "KILL", ticker)
    ok = len(steps) == 6 and all(s.ok for s in steps)
    result = {
        "ts": datetime.now(UTC).isoformat(),
        "env": venue.env,
        "subaccount": args.subaccount,
        "ticker": ticker,
        "ok": ok,
        "steps": [asdict(s) for s in steps],
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(f"{'all steps passed' if ok else 'FAILED'}; results: {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

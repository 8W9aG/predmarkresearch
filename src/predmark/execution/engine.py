"""Order management: risk checks, a write-ahead journal, fill sync, and reconciliation.

Safety behaviour, all of it fail-closed:
- Every order is journaled before it is sent, so a crash cannot lose an in-flight order.
- An ambiguous send is never retried. It halts the engine until reconcile() resolves it.
- Fills are idempotent by fill id, and the ledger is rebuilt from the journal on restart.
- A kill file, a daily-loss breach, or a reconciliation mismatch halts and cancels resting orders.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

from .models import (
    TERMINAL,
    Book,
    Fill,
    OrderRequest,
    OrderStatus,
    Side,
    VenueRejected,
    VenueUnknown,
)
from .risk import Ledger, RiskLimits, check_order
from .venue import Venue

log = logging.getLogger(__name__)
D = Decimal
UNKNOWN_GRACE_SECONDS = 30.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
  client_order_id TEXT PRIMARY KEY, venue_order_id TEXT, ticker TEXT NOT NULL, side TEXT NOT NULL,
  count TEXT NOT NULL, price TEXT NOT NULL, tif TEXT NOT NULL, post_only INTEGER NOT NULL,
  strategy TEXT NOT NULL, status TEXT NOT NULL, reason TEXT, created_ts REAL NOT NULL,
  updated_ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS fills (
  fill_id TEXT PRIMARY KEY, order_id TEXT NOT NULL, ticker TEXT NOT NULL, side TEXT NOT NULL,
  count TEXT NOT NULL, price TEXT NOT NULL, fee TEXT NOT NULL, is_taker INTEGER NOT NULL,
  ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS settlements (ticker TEXT PRIMARY KEY, yes_value TEXT NOT NULL,
  ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS events (ts REAL NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS orders_created ON orders(created_ts);
CREATE INDEX IF NOT EXISTS fills_order ON fills(order_id);
"""


class Engine:
    def __init__(
        self,
        venue: Venue,
        journal_path: str | Path,
        limits: RiskLimits | None = None,
        kill_file: str | Path = "data/KILL",
        clock: Callable[[], float] = time.time,
    ):
        self.venue = venue
        self.limits = limits or RiskLimits()
        self.kill_file = Path(kill_file)
        self.clock = clock
        Path(journal_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(journal_path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.ledger = Ledger()
        self._rebuild_ledger()

    # --- state ---------------------------------------------------------------------------

    def _rebuild_ledger(self) -> None:
        for r in self.db.execute("SELECT * FROM fills ORDER BY ts, fill_id"):
            self.ledger.apply(_row_fill(r))
        for r in self.db.execute("SELECT * FROM settlements ORDER BY ts"):
            self.ledger.settle(r["ticker"], D(r["yes_value"]))

    def _meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def _set_meta(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))
        self.db.commit()

    def _event(self, kind: str, **detail: object) -> None:
        self.db.execute(
            "INSERT INTO events VALUES (?, ?, ?)",
            (self.clock(), kind, json.dumps(detail, default=str)),
        )
        self.db.commit()
        log.info("%s %s", kind, detail)

    @property
    def halt_reason(self) -> str | None:
        if self.kill_file.exists():
            return f"kill file {self.kill_file} present"
        return self._meta("halted")

    def _set_status(
        self,
        cid: str,
        status: OrderStatus,
        venue_order_id: str | None = None,
        reason: str | None = None,
    ) -> None:
        self.db.execute(
            "UPDATE orders SET status = ?, venue_order_id = COALESCE(?, venue_order_id), "
            "reason = COALESCE(?, reason), updated_ts = ? WHERE client_order_id = ?",
            (status.value, venue_order_id, reason, self.clock(), cid),
        )
        self.db.commit()

    def _live_rows(self) -> list[sqlite3.Row]:
        """Orders that may still fill, including ones whose send outcome is unknown."""
        return self.db.execute(
            "SELECT * FROM orders WHERE status IN (?, ?, ?)",
            (OrderStatus.PENDING_NEW.value, OrderStatus.RESTING.value, OrderStatus.UNKNOWN.value),
        ).fetchall()

    def _open_exposure(self) -> dict[str, list[tuple[Side, Decimal, Decimal]]]:
        out: dict[str, list[tuple[Side, Decimal, Decimal]]] = {}
        live = self._live_rows()
        ids = [r["venue_order_id"] for r in live if r["venue_order_id"]]
        filled: dict[str, Decimal] = {}
        for start in range(0, len(ids), 500):  # only live orders' fills, so cost does not grow
            chunk = ids[start : start + 500]
            marks = ",".join("?" * len(chunk))
            query = f"SELECT order_id, count FROM fills WHERE order_id IN ({marks})"
            for r in self.db.execute(query, chunk):
                filled[r["order_id"]] = filled.get(r["order_id"], D(0)) + D(r["count"])
        for r in live:
            remaining = D(r["count"]) - filled.get(r["venue_order_id"] or "", D(0))
            if remaining > 0:
                out.setdefault(r["ticker"], []).append((Side(r["side"]), remaining, D(r["price"])))
        return out

    # --- trading -------------------------------------------------------------------------

    def submit(self, req: OrderRequest) -> OrderStatus:
        now = self.clock()
        reason = self.halt_reason
        if reason:
            return self._record_rejection(req, f"halted: {reason}")
        book = self.venue.book(req.ticker)
        orders_last_minute = self.db.execute(
            "SELECT COUNT(*) AS n FROM orders WHERE created_ts > ? AND status != ?",
            (now - 60, OrderStatus.REJECTED.value),
        ).fetchone()["n"]
        reason = check_order(
            req, self.limits, self.ledger, self._open_exposure(), book, now, orders_last_minute
        )
        if reason:
            return self._record_rejection(req, reason)

        self._insert_order(req, OrderStatus.PENDING_NEW, None)
        try:
            result = self.venue.place(req)
        except VenueRejected as exc:
            self._set_status(req.client_order_id, OrderStatus.REJECTED, reason=str(exc))
            self._event("venue_rejected", cid=req.client_order_id, reason=str(exc))
            return OrderStatus.REJECTED
        except VenueUnknown as exc:
            self._set_status(req.client_order_id, OrderStatus.UNKNOWN, reason=str(exc))
            self.halt(f"unknown send outcome for {req.client_order_id}: {exc}", cancel=False)
            return OrderStatus.UNKNOWN
        self._set_status(req.client_order_id, result.status, venue_order_id=result.order_id)
        self.sync_fills()
        return result.status

    def _insert_order(self, req: OrderRequest, status: OrderStatus, reason: str | None) -> None:
        now = self.clock()
        self.db.execute(
            "INSERT INTO orders VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                req.client_order_id,
                req.ticker,
                req.side.value,
                str(req.count),
                str(req.price),
                req.tif.value,
                int(req.post_only),
                req.strategy,
                status.value,
                reason,
                now,
                now,
            ),
        )
        self.db.commit()

    def _record_rejection(self, req: OrderRequest, reason: str) -> OrderStatus:
        self._insert_order(req, OrderStatus.REJECTED, reason)
        self._event("risk_rejected", cid=req.client_order_id, reason=reason)
        return OrderStatus.REJECTED

    def cancel(self, client_order_id: str) -> bool:
        row = self.db.execute(
            "SELECT * FROM orders WHERE client_order_id = ?", (client_order_id,)
        ).fetchone()
        if row is None or row["venue_order_id"] is None:
            return False
        if OrderStatus(row["status"]) in TERMINAL:
            return False
        try:
            self.venue.cancel(row["venue_order_id"], row["ticker"])
        except VenueRejected as exc:
            self._event("cancel_rejected", cid=client_order_id, reason=str(exc))
            self.sync_fills()
            return False
        except VenueUnknown as exc:
            self.halt(f"unknown cancel outcome for {client_order_id}: {exc}", cancel=False)
            return False
        self._set_status(client_order_id, OrderStatus.CANCELED)
        self.sync_fills()
        return True

    def cancel_all(self) -> int:
        """Cancel everything the venue reports as open, including orders we do not recognise."""
        n = 0
        for order in self.venue.open_orders():
            try:
                self.venue.cancel(order.order_id, order.ticker)
            except (VenueRejected, VenueUnknown) as exc:
                self._event("cancel_failed", order_id=order.order_id, reason=str(exc))
                continue
            n += 1
            if order.client_order_id:
                self._set_status(order.client_order_id, OrderStatus.CANCELED)
        self.sync_fills()
        return n

    def halt(self, reason: str, cancel: bool = True) -> None:
        self._set_meta("halted", reason)
        self._event("halt", reason=reason)
        if cancel:
            self.cancel_all()

    def resume(self) -> bool:
        """Clear a halt, but only if the venue and the journal agree."""
        problems = self.reconcile()
        if problems or self.kill_file.exists():
            return False
        self.db.execute("DELETE FROM meta WHERE key = 'halted'")
        self.db.commit()
        self._event("resume")
        return True

    # --- synchronisation -----------------------------------------------------------------

    def sync_fills(self) -> int:
        cursor = float(self._meta("fills_cursor") or 0)
        new = 0
        latest = cursor
        for fill in self.venue.fills(max(cursor - 60, 0)):  # overlap; dedupe is by fill id
            cur = self.db.execute(
                "INSERT OR IGNORE INTO fills VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    fill.fill_id,
                    fill.order_id,
                    fill.ticker,
                    fill.side.value,
                    str(fill.count),
                    str(fill.price),
                    str(fill.fee),
                    int(fill.is_taker),
                    fill.ts,
                ),
            )
            latest = max(latest, fill.ts)
            if cur.rowcount:
                self.ledger.apply(fill)
                new += 1
        self.db.commit()
        if latest != cursor:
            self._set_meta("fills_cursor", str(latest))
        self._mark_filled_orders()
        return new

    def _mark_filled_orders(self) -> None:
        for r in self._live_rows():
            if r["venue_order_id"] is None:
                continue
            filled = self.db.execute(
                "SELECT count FROM fills WHERE order_id = ?", (r["venue_order_id"],)
            ).fetchall()
            if sum((D(f["count"]) for f in filled), D(0)) >= D(r["count"]):
                self._set_status(r["client_order_id"], OrderStatus.FILLED)

    def tick(self) -> None:
        """One maintenance pass: refresh books for live tickers, sync fills, check daily loss."""
        tickers = set(self.ledger.markets) | {r["ticker"] for r in self._live_rows()}
        books = {t: self.venue.book(t) for t in tickers}
        self.sync_fills()
        self.check_daily_loss(books)

    def apply_settlement(self, ticker: str, yes_value: Decimal) -> None:
        cur = self.db.execute(
            "INSERT OR IGNORE INTO settlements VALUES (?, ?, ?)",
            (ticker, str(yes_value), self.clock()),
        )
        self.db.commit()
        if cur.rowcount:
            self.ledger.settle(ticker, yes_value)
            self._event("settled", ticker=ticker, yes_value=yes_value)

    def check_daily_loss(self, books: dict[str, Book] | None = None) -> Decimal:
        """Mark at liquidation value and halt if the day's loss limit is breached."""
        if books is None:
            books = {t: self.venue.book(t) for t in self.ledger.markets}
        nav = self.ledger.nav(books)
        day = str(int(self.clock() // 86400))
        if self._meta("nav_day") != day:
            self._set_meta("nav_day", day)
            self._set_meta("nav_day_start", str(nav))
        loss = D(self._meta("nav_day_start") or "0") - nav
        if loss > self.limits.max_daily_loss and not self._meta("halted"):
            self.halt(f"daily loss {loss} above {self.limits.max_daily_loss}")
        return loss

    def reconcile(self) -> list[str]:
        """Compare venue truth with the journal, resolving UNKNOWN sends. Returns any mismatch."""
        self.sync_fills()
        problems: list[str] = []
        now = self.clock()

        for r in self.db.execute(
            "SELECT * FROM orders WHERE status IN (?, ?)",
            (OrderStatus.UNKNOWN.value, OrderStatus.PENDING_NEW.value),
        ).fetchall():
            cid = r["client_order_id"]
            found = self.venue.find_order(cid, r["created_ts"] - 60)
            if found is not None:
                self._set_status(cid, found.status, venue_order_id=found.order_id)
            elif now - r["created_ts"] > UNKNOWN_GRACE_SECONDS:
                self._set_status(cid, OrderStatus.REJECTED, reason="not found at venue")
            else:
                problems.append(f"order {cid} unresolved, still inside the grace period")
        self.sync_fills()

        venue_open = {o.order_id: o for o in self.venue.open_orders()}
        local = {
            r["venue_order_id"]: r
            for r in self.db.execute(
                "SELECT * FROM orders WHERE status = ?", (OrderStatus.RESTING.value,)
            ).fetchall()
        }
        for oid in venue_open.keys() - local.keys():
            problems.append(f"venue order {oid} is not in the journal")
        for oid in local.keys() - venue_open.keys():
            r = local[oid]
            filled = self.db.execute(
                "SELECT count FROM fills WHERE order_id = ?", (oid,)
            ).fetchall()
            if sum((D(f["count"]) for f in filled), D(0)) < D(r["count"]):
                self._set_status(r["client_order_id"], OrderStatus.CANCELED, reason="gone")

        venue_pos = {t: q for t, q in self.venue.positions().items() if q != 0}
        local_pos = {t: p.contracts for t, p in self.ledger.markets.items() if p.contracts != 0}
        for ticker in venue_pos.keys() | local_pos.keys():
            at_venue, in_journal = venue_pos.get(ticker, D(0)), local_pos.get(ticker, D(0))
            if at_venue != in_journal:
                problems.append(
                    f"position mismatch on {ticker}: venue {at_venue}, journal {in_journal}"
                )

        if problems:
            self._event("reconcile_mismatch", problems=problems)
            if not self._meta("halted"):
                self.halt("reconciliation mismatch: " + "; ".join(problems), cancel=False)
        return problems


def _row_fill(r: sqlite3.Row) -> Fill:
    return Fill(
        fill_id=r["fill_id"],
        order_id=r["order_id"],
        ticker=r["ticker"],
        side=Side(r["side"]),
        count=D(r["count"]),
        price=D(r["price"]),
        fee=D(r["fee"]),
        is_taker=bool(r["is_taker"]),
        ts=r["ts"],
    )

"""Record order book snapshots: history the venues do not serve, so it can only be collected."""

from __future__ import annotations

import gzip
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .kalshi import KalshiData
from .polymarket import PolymarketData


def kalshi_universe(data: KalshiData, top_n: int) -> list[str]:
    """Return the tickers of the `top_n` open markets by 24-hour volume, from the first 3 pages."""
    markets = data.open_markets(limit_pages=3)
    markets.sort(key=lambda m: float(m.get("volume_24h_fp") or 0), reverse=True)
    return [m["ticker"] for m in markets[:top_n]]


def record(
    venue: str,
    top_n: int = 50,
    interval_seconds: float = 60,
    iterations: int | None = None,
    universe_refresh_seconds: float = 3600,
    out_dir: str | Path = "data/books",
    log: Callable[[str], None] = print,
) -> None:
    """Poll the order books of a venue's busiest markets and append them to daily gzip files.

    Writes `<out_dir>/<venue>/<UTC date>.jsonl.gz`. The market list is refreshed every
    `universe_refresh_seconds`. With `iterations=None` it runs until stopped. A failed book is
    logged and skipped.
    """
    if venue == "kalshi":
        kalshi = KalshiData(per_second=5)
        universe = lambda: kalshi_universe(kalshi, top_n)  # noqa: E731
        snap: Callable[[str], Any] = kalshi.orderbook
    elif venue == "polymarket":
        poly = PolymarketData()
        universe = lambda: [t for t, _ in poly.top_token_ids(top_n)]  # noqa: E731
        snap = poly.book
    else:
        raise ValueError(f"unknown venue {venue}")

    out = Path(out_dir) / venue
    out.mkdir(parents=True, exist_ok=True)
    tickers: list[str] = []
    refreshed = 0.0
    done = 0
    while iterations is None or done < iterations:
        started = time.time()
        if started - refreshed > universe_refresh_seconds:
            tickers = universe()
            refreshed = started
        path = out / f"{datetime.now(UTC):%Y-%m-%d}.jsonl.gz"
        ok = failed = 0
        with gzip.open(path, "at") as fh:
            for ticker in tickers:
                try:
                    book = snap(ticker)
                except Exception as exc:  # noqa: BLE001 - one bad market must not end a recording
                    failed += 1
                    log(f"{ticker}: {exc}")
                    continue
                fh.write(json.dumps({"ts": time.time(), "ticker": ticker, "book": book}) + "\n")
                ok += 1
        done += 1
        log(f"{venue}: {ok} books, {failed} failed -> {path}")
        if iterations is None or done < iterations:
            time.sleep(max(0.0, interval_seconds - (time.time() - started)))

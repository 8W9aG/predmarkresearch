"""Kalshi listing snapshots: the quote, sizes and 24-hour volume of every popular open market, once
a day. Kalshi does not serve historical order books or listings, so a backtest that replays what the
live paper run saw needs this record.

A snapshot keeps every market with 24-hour volume of at least MIN_VOLUME_24H, busiest first, in the
order Nothing Ever Happens uses. It does not apply the TOP cap. Each record has its `rank`, so a
replay can apply the cap or another one. The strategy pass fetches its own listing a few minutes
earlier in the same daily job, so quotes can differ a little from what the pass saw.

Each run appends to `snapshots/<UTC date>.jsonl.gz`, one market per line, with the listing time.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..strategies.nothing_ever_happens import MIN_VOLUME_24H, popular
from .base import Collector

NAME = "kalshi-listing"
# Listing fields kept per market. Rules text and other long fields are left out to keep files small.
FIELDS = (
    "ticker",
    "event_ticker",
    "title",
    "yes_sub_title",
    "status",
    "open_time",
    "close_time",
    "yes_bid_dollars",
    "yes_ask_dollars",
    "yes_bid_size_fp",
    "yes_ask_size_fp",
    "last_price_dollars",
    "volume_24h_fp",
    "volume_fp",
    "open_interest_fp",
)


def snapshot(
    markets: list[dict[str, Any]], listed_at: datetime, min_volume: float = MIN_VOLUME_24H
) -> list[dict[str, Any]]:
    """The records for one listing: popular markets, busiest first, each with its 1-based rank."""
    ts = listed_at.isoformat(timespec="seconds")
    return [
        {"listed_at": ts, "rank": rank, **{k: m.get(k) for k in FIELDS}}
        for rank, m in enumerate(popular(markets, min_volume, top=None), start=1)
    ]


def snapshot_path(out_dir: Path, listed_at: datetime) -> Path:
    return out_dir / "snapshots" / f"{listed_at.astimezone(UTC):%Y-%m-%d}.jsonl.gz"


def read(path: str | Path) -> Iterator[dict[str, Any]]:
    """Every record in one snapshot file, including files appended to by more than one run."""
    with gzip.open(path, "rt") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def run(
    out_dir: Path,
    log: Callable[[str], None] = print,
    min_volume: float = MIN_VOLUME_24H,
    data: Any = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, Any]:
    from ..data.kalshi import KalshiData

    data = data or KalshiData(per_second=10)
    listed_at = clock()
    markets = data.open_markets(limit_pages=None)
    records = snapshot(markets, listed_at, float(min_volume))
    path = snapshot_path(out_dir, listed_at)
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "at") as fh:
        for record in records:
            fh.write(json.dumps(record, sort_keys=True, default=str) + "\n")
    log(f"kalshi listing: {len(records)} of {len(markets)} open markets -> {path}")
    return {
        "listed_at": listed_at.isoformat(timespec="seconds"),
        "listed": len(markets),
        "recorded": len(records),
        "min_volume_24h": min_volume,
        "path": str(path.relative_to(out_dir)),
    }


COLLECTOR = Collector(
    name=NAME,
    description="daily quotes, sizes and 24h volume of popular Kalshi markets, for replay",
    run=run,
)

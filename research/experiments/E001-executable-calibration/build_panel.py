"""Build the E001 quote panel. Resumable: finished markets are kept in data/e001/raw.jsonl."""

from __future__ import annotations

import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from predmark.data.kalshi import KalshiData

CATEGORIES = {"Economics", "Climate and Weather", "Financials", "Crypto", "Commodities"}
FREQUENCIES = {"daily", "weekly", "monthly"}
START, END = "2025-01-01", "2026-11-01"
MAX_EVENTS, MAX_MARKETS, BUDGET = 40, 6, 6000
HORIZON = 12 * 3600
OUT = Path("data/e001")


def h(text: str) -> str:
    """Return a SHA-256 hex digest of the text, salted with the experiment ID."""
    return hashlib.sha256(f"E001:{text}".encode()).hexdigest()


def ts(value: str) -> int:
    """Return the Unix time in seconds for an ISO 8601 timestamp."""
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def universe(data: KalshiData) -> list[dict]:
    """Return the candidate markets, sampled in hash order and cached in universe.json.

    Each series gives at most MAX_EVENTS events and each event at most MAX_MARKETS
    markets. The total is capped at BUDGET. A series whose listing fails is skipped.
    """
    path = OUT / "universe.json"
    if path.exists():
        return json.loads(path.read_text())
    series = [
        s
        for s in data.series()
        if s.get("category") in CATEGORIES and s.get("frequency") in FREQUENCIES
    ]
    print(f"{len(series)} series in scope", flush=True)
    candidates = []
    for i, s in enumerate(sorted(series, key=lambda x: x["ticker"])):
        if i % 50 == 0:
            print(f"listing {i}/{len(series)}, {len(candidates)} candidates", flush=True)
        events: dict[str, list[dict]] = defaultdict(list)
        try:
            for m in data.settled_markets(s["ticker"]):
                if (
                    m.get("market_type") == "binary"
                    and m.get("result") in {"yes", "no"}
                    and START <= (m.get("close_time") or "") < END
                ):
                    events[m["event_ticker"]].append(m)
        except Exception as exc:  # noqa: BLE001 - logged, and the series is simply absent
            print(f"{s['ticker']}: listing failed: {exc}", flush=True)
            continue
        ranked = sorted(events, key=h)[:MAX_EVENTS]
        for rank, event in enumerate(ranked):
            for m in sorted(events[event], key=lambda x: h(x["ticker"]))[:MAX_MARKETS]:
                candidates.append(
                    {
                        "rank": rank,
                        "hash": h(event),
                        "series": s["ticker"],
                        "category": s["category"],
                        "frequency": s["frequency"],
                        "fee_type": s.get("fee_type"),
                        "fee_multiplier": s.get("fee_multiplier"),
                        "event": event,
                        "ticker": m["ticker"],
                        "close_time": m["close_time"],
                        "result": m["result"],
                    }
                )
    candidates.sort(key=lambda c: (c["rank"], c["hash"], h(c["ticker"])))
    candidates = candidates[:BUDGET]
    OUT.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(candidates))
    return candidates


def main() -> int:
    """Fetch hourly candles ending 12 hours before close for each candidate, and return 0.

    Rows are appended to raw.jsonl. Tickers already there are skipped, and a failed
    request is recorded in the row's error field.
    """
    OUT.mkdir(parents=True, exist_ok=True)
    data = KalshiData(per_second=8)
    candidates = universe(data)
    cutoff = data.historical_cutoff()["market_settled_ts"]
    raw = OUT / "raw.jsonl"
    done = set()
    if raw.exists():
        done = {json.loads(line)["ticker"] for line in raw.read_text().splitlines()}
    failures = 0
    with raw.open("a") as fh:
        for i, c in enumerate(candidates):
            if c["ticker"] in done:
                continue
            close = ts(c["close_time"])
            try:
                candles = data.candlesticks(
                    c["series"],
                    c["ticker"],
                    close - HORIZON - 4 * 3600,
                    close - HORIZON,
                    60,
                    historical=c["close_time"] < cutoff,
                )
                error = None
            except Exception as exc:  # noqa: BLE001 - recorded per market, counted in the report
                candles, error = [], f"{type(exc).__name__}: {exc}"[:200]
                failures += 1
            fh.write(json.dumps({**c, "candles": candles, "error": error}) + "\n")
            fh.flush()
            if i % 250 == 0:
                print(f"candles {i}/{len(candidates)}, failures {failures}", flush=True)
    print(f"done: {len(candidates)} candidates, {failures} failures this run", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

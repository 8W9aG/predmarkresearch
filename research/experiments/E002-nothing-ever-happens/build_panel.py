"""Build the E002 panel. Resumable: finished markets are kept in data/e002/raw.jsonl."""

from __future__ import annotations

import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from predmark.data.kalshi import KalshiData

N_SERIES = 1500
START, END, HOLDOUT_START = "2025-01-01", "2026-11-06", "2026-11-01"
BUDGET = {"dev": 10_000, "holdout": 6_000}
WINDOW = 24 * 3600
OUT = Path("data/e002")


def h(text: str) -> str:
    """Return a SHA-256 hex digest of the text, salted with the experiment ID."""
    return hashlib.sha256(f"E002:{text}".encode()).hexdigest()


def ts(value: str) -> int:
    """Return the Unix time in seconds for an ISO 8601 timestamp."""
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def sample_events(markets: list[dict], budget: dict[str, int] = BUDGET) -> list[dict]:
    """Take whole events in hash order until each split's budget is reached."""
    events: dict[str, list[dict]] = defaultdict(list)
    for market in markets:
        events[market["event"]].append(market)
    chosen: list[dict] = []
    used = {split: 0 for split in budget}
    for event in sorted(events, key=h):
        rows = events[event]
        split = "holdout" if max(r["close_time"] for r in rows) >= HOLDOUT_START else "dev"
        if used[split] + len(rows) > budget[split]:
            continue
        used[split] += len(rows)
        chosen.extend({**r, "split": split, "event_size": len(rows)} for r in rows)
    return chosen


def universe(data: KalshiData) -> list[dict]:
    """Return the sampled markets from the first N_SERIES series in hash order.

    Writes manifest.json and universe.json, and reuses universe.json if it exists.
    """
    path = OUT / "universe.json"
    if path.exists():
        return json.loads(path.read_text())
    series = sorted(data.series(), key=lambda s: h(s["ticker"]))
    total_series = len(series)
    series = series[:N_SERIES]
    markets: list[dict] = []
    failed: list[str] = []
    unsettled = 0
    for i, s in enumerate(series):
        if i % 100 == 0:
            print(f"listing {i}/{len(series)}, {len(markets)} markets", flush=True)
        try:
            for m in data.settled_markets(s["ticker"]):
                if (
                    m.get("market_type") == "binary"
                    and m.get("result") in {"yes", "no"}
                    and START <= (m.get("close_time") or "") < END
                ):
                    markets.append(
                        {
                            "series": s["ticker"],
                            "category": s.get("category"),
                            "frequency": s.get("frequency"),
                            "fee_multiplier": s.get("fee_multiplier"),
                            "event": m["event_ticker"],
                            "ticker": m["ticker"],
                            "open_time": m["open_time"],
                            "close_time": m["close_time"],
                            "result": m["result"],
                        }
                    )
            params = {"series_ticker": s["ticker"], "status": "closed", "limit": 1000}
            closed = data.client.get_json("/markets", params).get("markets") or []
            unsettled += sum(START <= (m.get("close_time") or "") < END for m in closed)
        except Exception as exc:  # noqa: BLE001 - reported in the manifest
            failed.append(f"{s['ticker']}: {exc}"[:200])
    manifest = {
        "total_series": total_series,
        "sampled_series": len(series),
        "failed_series": failed,
        "listed_markets": len(markets),
        "closed_not_settled": unsettled,
    }
    chosen = sample_events(markets)
    manifest["sampled_markets"] = len(chosen)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2))
    path.write_text(json.dumps(chosen))
    print(json.dumps({k: v for k, v in manifest.items() if k != "failed_series"}), flush=True)
    return chosen


def main() -> int:
    """Fetch hourly candles for each candidate's first 24 hours, and return 0.

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
            opened = ts(c["open_time"])
            try:
                candles = data.candlesticks(
                    c["series"],
                    c["ticker"],
                    opened,
                    min(opened + WINDOW, ts(c["close_time"])),
                    60,
                    historical=c["close_time"] < cutoff,
                )
                error = None
            except Exception as exc:  # noqa: BLE001 - recorded per market, counted in results
                candles, error = [], f"{type(exc).__name__}: {exc}"[:200]
                failures += 1
            fh.write(json.dumps({**c, "candles": candles, "error": error}) + "\n")
            fh.flush()
            if i % 500 == 0:
                print(f"candles {i}/{len(candidates)}, failures {failures}", flush=True)
    print(f"done: {len(candidates)} candidates, {failures} failures this run", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

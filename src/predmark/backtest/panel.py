"""Download a sample of settled Kalshi markets with hourly candles from their first hours.

Sampling follows E002: series in hash order, then whole events in hash order up to a budget, so
the sample does not depend on price, volume, or outcome. Downloads resume from `raw.jsonl`.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from ..data.kalshi import KalshiData


def to_ts(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def _hash(label: str, text: str) -> str:
    return hashlib.sha256(f"{label}:{text}".encode()).hexdigest()


def sample_events(markets: list[dict], max_markets: int, label: str) -> list[dict]:
    """Whole events in hash order until the budget is reached. Events too big to fit are skipped."""
    events: dict[str, list[dict]] = defaultdict(list)
    for market in markets:
        events[market["event"]].append(market)
    chosen: list[dict] = []
    for event in sorted(events, key=lambda e: _hash(label, e)):
        rows = events[event]
        if len(chosen) + len(rows) > max_markets:
            continue
        chosen.extend({**r, "event_size": len(rows)} for r in rows)
    return chosen


def build(
    out: str | Path,
    start: str,
    end: str,
    n_series: int = 300,
    max_markets: int = 3000,
    window_hours: int = 24,
    label: str = "backtest",
    data: KalshiData | None = None,
    log: Callable[[str], None] = print,
) -> Path:
    """Build or resume a panel in `out`. Returns the path of `raw.jsonl`."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    params = {
        "start": start,
        "end": end,
        "n_series": n_series,
        "max_markets": max_markets,
        "window_hours": window_hours,
        "label": label,
    }
    manifest_path = out / "manifest.json"
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text())["params"]
        if previous != params:
            raise ValueError(f"{out} was built with {previous}. Use another directory.")
    data = data or KalshiData(per_second=8)
    universe_path = out / "universe.json"
    if universe_path.exists():
        chosen = json.loads(universe_path.read_text())
    else:
        chosen = _universe(data, params, manifest_path, log)
        universe_path.write_text(json.dumps(chosen))

    raw = out / "raw.jsonl"
    done = set()
    if raw.exists():
        done = {json.loads(line)["ticker"] for line in raw.read_text().splitlines()}
    cutoff = data.historical_cutoff()["market_settled_ts"]
    window = window_hours * 3600
    with raw.open("a") as fh:
        for i, c in enumerate(chosen):
            if c["ticker"] in done:
                continue
            opened = to_ts(c["open_time"])
            try:
                candles = data.candlesticks(
                    c["series"],
                    c["ticker"],
                    opened,
                    min(opened + window, to_ts(c["close_time"])),
                    60,
                    historical=c["close_time"] < cutoff,
                )
                error = None
            except Exception as exc:  # noqa: BLE001 - recorded per market, counted in results
                candles, error = [], f"{type(exc).__name__}: {exc}"[:200]
            fh.write(json.dumps({**c, "candles": candles, "error": error}) + "\n")
            fh.flush()
            if i % 500 == 0:
                log(f"candles {i}/{len(chosen)}")
    return raw


def _universe(data: KalshiData, params: dict, manifest_path: Path, log) -> list[dict[str, Any]]:
    label = params["label"]
    every = data.series()
    series = sorted(every, key=lambda s: _hash(label, s["ticker"]))[: params["n_series"]]
    markets: list[dict] = []
    failed: list[str] = []
    unsettled = 0
    for i, s in enumerate(series):
        if i % 100 == 0:
            log(f"listing series {i}/{len(series)}, {len(markets)} markets")
        try:
            for m in data.settled_markets(s["ticker"]):
                close = m.get("close_time") or ""
                if (
                    m.get("market_type") == "binary"
                    and m.get("result") in {"yes", "no"}
                    and params["start"] <= close < params["end"]
                ):
                    markets.append(
                        {
                            "series": s["ticker"],
                            "category": s.get("category"),
                            "fee_multiplier": s.get("fee_multiplier"),
                            "event": m["event_ticker"],
                            "ticker": m["ticker"],
                            "open_time": m["open_time"],
                            "close_time": m["close_time"],
                            "result": m["result"],
                        }
                    )
            query = {"series_ticker": s["ticker"], "status": "closed", "limit": 1000}
            closed = data.client.get_json("/markets", query).get("markets") or []
            unsettled += sum(
                params["start"] <= (m.get("close_time") or "") < params["end"] for m in closed
            )
        except Exception as exc:  # noqa: BLE001 - reported in the manifest
            failed.append(f"{s['ticker']}: {exc}"[:200])
    chosen = sample_events(markets, params["max_markets"], label)
    manifest = {
        "params": params,
        "total_series": len(every),
        "sampled_series": len(series),
        "failed_series": failed,
        "listed_markets": len(markets),
        "closed_not_settled": unsettled,
        "sampled_markets": len(chosen),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))
    return chosen

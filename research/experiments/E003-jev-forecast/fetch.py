"""Fetch E003's data into data/e003/: the collected forecasts, then outcomes for one split.

    uv run python research/experiments/E003-jev-forecast/fetch.py dev
    uv run python research/experiments/E003-jev-forecast/fetch.py holdout   # on or after the cutoff

Forecasts and the Jev cost log come from the `predmark-paper` Modal volume. Outcomes and series fees
come from Kalshi, only for forecasts in the requested split, so holdout outcomes are never on disk
before the holdout may be opened.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from analyze import CUTOFF, DATA, split_of  # noqa: E402

VOLUME = "predmark-paper"
REMOTE = "collect/jev-forecasts"
MARKET_FIELDS = ("ticker", "status", "result", "settlement_ts", "close_time", "event_ticker")
SERIES_FIELDS = ("ticker", "category", "fee_type", "fee_multiplier")


def series_of(event_ticker: str) -> str:
    """Kalshi event tickers start with their series ticker, up to the first dash."""
    return event_ticker.split("-", 1)[0]


def pull_volume(dest: Path) -> None:
    """Copy forecasts.jsonl and cost.jsonl from the Modal volume into `dest`."""
    import modal

    vol = modal.Volume.from_name(VOLUME)
    for name in ("forecasts.jsonl", "cost.jsonl"):
        (dest / name).write_bytes(b"".join(vol.read_file(f"{REMOTE}/{name}")))


def pull_outcomes(dest: Path, split: str) -> dict:
    """Write outcomes for the split's tickers and fees for every series, and return counts.

    Only the split's outcomes are fetched. Series rows cover all forecast events.
    """
    from predmark.data.kalshi import KalshiData

    rows = [json.loads(x) for x in (dest / "forecasts.jsonl").read_text().splitlines() if x]
    tickers = sorted({r["ticker"] for r in rows if split_of(r["ts"]) == split})
    data = KalshiData(per_second=10)
    found = data.markets(tickers)
    with (dest / f"outcomes_{split}.jsonl").open("w") as fh:
        for t in tickers:
            m = found.get(t)
            out = {k: m.get(k) for k in MARKET_FIELDS} if m else {"ticker": t, "missing": True}
            fh.write(json.dumps(out, sort_keys=True) + "\n")
    events = {r["event_ticker"] for r in rows if r.get("event_ticker")}
    series = sorted({series_of(e) for e in events})
    with (dest / "series.jsonl").open("w") as fh:
        for s in series:
            try:
                info = data.client.get_json(f"/series/{s}")["series"]
            except Exception as exc:  # noqa: BLE001 - recorded, and analysis falls back to 1
                info = {"ticker": s, "error": f"{type(exc).__name__}: {exc}"}
            fh.write(json.dumps({k: info.get(k) for k in (*SERIES_FIELDS, "error")}) + "\n")
    return {"tickers": len(tickers), "missing": len(tickers) - len(found), "series": len(series)}


def main(argv: list[str] | None = None, today: date | None = None) -> int:
    """Fetch the forecasts and one split's outcomes, write a manifest, and return 0.

    Holdout outcomes are refused before CUTOFF.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("split", choices=("dev", "holdout"))
    parser.add_argument(
        "--skip-volume", action="store_true", help="reuse data/e003/forecasts.jsonl"
    )
    args = parser.parse_args(argv)
    today = today or datetime.now(UTC).date()
    if args.split == "holdout" and today < date.fromisoformat(CUTOFF):
        raise SystemExit(f"holdout outcomes may not be pulled before {CUTOFF}")
    DATA.mkdir(parents=True, exist_ok=True)
    if not args.skip_volume:
        pull_volume(DATA)
    flow = pull_outcomes(DATA, args.split)
    manifest = {"split": args.split, "fetched_at": datetime.now(UTC).isoformat(), **flow}
    (DATA / f"manifest_{args.split}.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

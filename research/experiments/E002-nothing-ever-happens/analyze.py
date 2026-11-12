"""Evaluate E002 exactly as preregistered. Writes results.json beside this file."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

from predmark.execution.fees import kalshi_fee
from predmark.research import ledger
from predmark.research.metrics import cluster_bootstrap, interval

HERE = Path(__file__).parent
RAW = Path("data/e002/raw.jsonl")
EXPERIMENT = "E002"
WINDOW = 24 * 3600
N_BOOT = 2000
HOLDOUT_WINDOW = ("2026-11-01", "2026-11-05")
SIZE_BINS = [(1, 1), (2, 2), (3, 5), (6, 10), (11, 10**9)]


def to_ts(value: str) -> int:
    """Return the Unix time in seconds for an ISO 8601 timestamp."""
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def close_price(distribution: dict | None) -> float | None:
    """Live candles name this field close_dollars; archived candles name it close."""
    if not distribution:
        return None
    value = distribution.get("close_dollars", distribution.get("close"))
    return None if value is None else float(value)


def entry(row: dict) -> tuple[float, float | None] | None:
    """The earliest eligible candle with a bid, as (yes_bid, yes_ask). None if never tradable."""
    opened, closes = to_ts(row["open_time"]), to_ts(row["close_time"])
    candles = sorted(row["candles"], key=lambda c: c["end_period_ts"])
    for candle in candles:
        end = candle["end_period_ts"]
        if not (opened < end <= opened + WINDOW and end < closes):
            continue
        bid = close_price(candle.get("yes_bid"))
        if bid is not None and bid > 0:
            return bid, close_price(candle.get("yes_ask"))
    return None


def taker_fee(price: float, multiplier: object) -> float:
    """Return the Kalshi taker fee per contract, in dollars, on a 100-contract order."""
    m = Decimal(str(multiplier if multiplier is not None else 1))
    return float(kalshi_fee(Decimal(100), Decimal(str(round(price, 4))), True, multiplier=m)) / 100


def build(rows: list[dict]) -> tuple[pd.DataFrame, dict[str, int]]:
    """Return one row per tradable market, with buy-No and buy-Yes net P&L, and row counts.

    No is bought at 1 - yes_bid at the entry candle. Yes is bought at yes_ask as a control.
    """
    flow = {
        "candidates": len(rows),
        "request_errors": sum(1 for r in rows if r.get("error")),
    }
    records = []
    for r in rows:
        found = entry(r)
        if found is None:
            continue
        bid, ask = found
        y = 1.0 if r["result"] == "yes" else 0.0
        no_price = 1 - bid
        fee = taker_fee(no_price, r.get("fee_multiplier"))
        records.append(
            {
                **{k: r.get(k) for k in ("series", "category", "event", "ticker", "split")},
                "event_size": r.get("event_size", 1),
                "date": r["close_time"][:10],
                "y": y,
                "bid": bid,
                "ask": ask,
                "no_price": no_price,
                "gross": (1 - y) - no_price,
                "net": (1 - y) - no_price - fee,
                "yes_net": (
                    y - ask - taker_fee(ask, r.get("fee_multiplier"))
                    if ask is not None and ask < 1
                    else np.nan
                ),
            }
        )
    df = pd.DataFrame(records)
    flow["traded"] = len(df)
    flow["never_tradable"] = flow["candidates"] - len(df)
    return df, flow


def mean_ci(df: pd.DataFrame, column: str, cluster: str) -> tuple[float, float]:
    """Return a 95% cluster-bootstrap interval for the mean of a column."""
    values = df[column].to_numpy()
    draws = cluster_bootstrap(lambda i, v=values: v[i].mean(), df[cluster].to_numpy(), N_BOOT)
    return interval(draws, 0.95)


def group(df: pd.DataFrame) -> dict:
    """Return summary statistics for a set of buy-No trades."""
    return {
        "n": int(len(df)),
        "events": int(df["event"].nunique()),
        "mean_net": float(df["net"].mean()),
        "no_win_rate": float((df["y"] == 0).mean()),
        "mean_no_price": float(df["no_price"].mean()),
        "roc": float(df["net"].sum() / df["no_price"].sum()),
    }


def evaluate(df: pd.DataFrame, clusters: tuple[str, ...]) -> dict:
    """Return the full summary for one split, with intervals clustered by each of `clusters`."""
    yes = df[df["yes_net"].notna()]
    result = {
        **group(df),
        "series": int(df["series"].nunique()),
        "dates": int(df["date"].nunique()),
        "first_close": df["date"].min(),
        "last_close": df["date"].max(),
        "mean_gross": float(df["gross"].mean()),
        "net_ci95": {c: mean_ci(df, "net", c) for c in clusters},
        "gross_ci95_event": mean_ci(df, "gross", "event"),
        "control_buy_yes": {
            "n": int(len(yes)),
            "mean_net": float(yes["yes_net"].mean()) if len(yes) else None,
            "ci95_event": mean_ci(yes, "yes_net", "event") if yes["event"].nunique() > 1 else None,
        },
        "by_category": {
            str(k): group(v) for k, v in df.groupby("category", dropna=False) if len(v) >= 50
        },
        "by_series": {str(k): group(v) for k, v in df.groupby("series") if len(v) >= 100},
        "by_no_price": {},
        "by_event_size": {},
    }
    bins = np.minimum((df["no_price"].to_numpy() * 10).astype(int), 9)
    for b in sorted(set(bins)):
        result["by_no_price"][f"{b / 10:.1f}-{(b + 1) / 10:.1f}"] = group(df[bins == b])
    for low, high in SIZE_BINS:
        sub = df[df["event_size"].between(low, high)]
        if len(sub):
            label = str(low) if low == high else f"{low}+" if high > 10**6 else f"{low}-{high}"
            result["by_event_size"][label] = group(sub)
    return result


def decide(result: dict) -> dict:
    """Return whether H1 is supported: the event-clustered lower bound is above zero.

    The result must have intervals clustered by both event and series.
    """
    event = result["net_ci95"]["event"][0] > 0
    series = result["net_ci95"]["series"][0] > 0
    return {"supported": bool(event), "robust_to_series_clustering": bool(event and series)}


def main() -> None:
    """Claim the holdout, evaluate the dev and holdout splits, and write results.json.

    Both evaluations go to the trial ledger, and the ledger refuses a second holdout claim.
    The strict-window holdout is descriptive only and does not decide H1.
    """
    ledger.claim_holdout(EXPERIMENT)
    raw_bytes = RAW.read_bytes()
    digest = hashlib.sha256(raw_bytes).hexdigest()
    df, flow = build([json.loads(line) for line in raw_bytes.decode().splitlines()])
    prereg = HERE / "preregistration.md"

    dev = evaluate(df[df["split"] == "dev"], ("event", "series", "date"))
    ledger.record_trial(
        EXPERIMENT, "dev", prereg, digest, {"n": dev["n"], "mean_net": dev["mean_net"]}
    )
    holdout = evaluate(df[df["split"] == "holdout"], ("event", "series"))
    decisions = {"H1": decide(holdout)}
    # Descriptive only, see amendments.md. H1 is decided on the split as registered.
    in_window = df[(df["split"] == "holdout") & df["date"].between(*HOLDOUT_WINDOW)]
    strict = evaluate(in_window, ("event", "series"))
    ledger.record_trial(
        EXPERIMENT,
        "holdout",
        prereg,
        digest,
        {"n": holdout["n"], "mean_net": holdout["mean_net"], "decisions": decisions},
    )
    manifest = Path("data/e002/manifest.json")
    results = {
        "experiment": EXPERIMENT,
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset_sha256": digest,
        "prereg_sha256": ledger.file_sha256(prereg),
        "manifest": {
            k: (len(v) if k == "failed_series" else v)
            for k, v in json.loads(manifest.read_text()).items()
        },
        "sample_flow": flow,
        "dev": dev,
        "holdout": holdout,
        "holdout_strict_window": strict,
        "decisions": decisions,
    }
    (HERE / "results.json").write_text(json.dumps(results, indent=2, default=float) + "\n")
    print(json.dumps({"flow": flow, "decisions": decisions}, indent=2))


if __name__ == "__main__":
    main()

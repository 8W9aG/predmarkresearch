"""Evaluate E001 exactly as preregistered. Writes results.json beside this file."""

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
from predmark.research.metrics import (
    brier,
    calibration_table,
    cluster_bootstrap,
    ece,
    interval,
    log_loss,
    recalibration,
)

HERE = Path(__file__).parent
RAW = Path("data/e001/raw.jsonl")
EXPERIMENT = "E001"
HORIZON = 12 * 3600
MAX_STALE = 3 * 3600
MAX_SPREAD = 0.10
MIN_SERIES = 5
N_BOOT = 2000


def to_ts(value: str) -> int:
    """Return the Unix time in seconds for an ISO 8601 timestamp."""
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def quote(row: dict) -> tuple[float, float] | None:
    """Return the latest (yes_bid, yes_ask) at most MAX_STALE before the 12-hour horizon.

    Return None if no candle falls in that window or its prices are missing.
    """
    target = to_ts(row["close_time"]) - HORIZON
    usable = [c for c in row["candles"] if target - MAX_STALE <= c["end_period_ts"] <= target]
    if not usable:
        return None
    latest = max(usable, key=lambda c: c["end_period_ts"])
    try:
        return close_price(latest["yes_bid"]), close_price(latest["yes_ask"])
    except (KeyError, TypeError, ValueError):
        return None


def close_price(distribution: dict) -> float:
    """Live candles name this field close_dollars; archived candles name it close."""
    value = (
        distribution["close_dollars"] if "close_dollars" in distribution else distribution["close"]
    )
    return float(value)


def build(rows: list[dict]) -> tuple[pd.DataFrame, dict[str, int]]:
    """Apply the preregistered filters and return the panel with row counts after each step."""
    flow: dict[str, int] = {"candidates": len(rows)}
    flow["request_errors"] = sum(1 for r in rows if r.get("error"))
    records = []
    for r in rows:
        q = quote(r)
        records.append({**{k: v for k, v in r.items() if k != "candles"}, "q": q})
    df = pd.DataFrame(records)
    df["close_ts"] = df["close_time"].map(to_ts)
    df["hour"] = df["close_ts"] // 3600 % 24

    counts = df.groupby("series")["ticker"].transform("size")
    df = df[counts >= MIN_SERIES].copy()
    flow["after_min_series"] = len(df)
    modal = df.groupby("series")["hour"].agg(lambda s: s.mode().iloc[0])
    distance = (df["hour"] - df["series"].map(modal)).abs()
    distance = np.minimum(distance, 24 - distance)
    df = df[distance <= 1].copy()
    flow["after_scheduled_close"] = len(df)

    df = df[df["q"].notna()].copy()
    flow["after_quote_found"] = len(df)
    df["bid"] = df["q"].map(lambda q: q[0])
    df["ask"] = df["q"].map(lambda q: q[1])
    df = df[(df["bid"] > 0) & (df["ask"] < 1) & (df["ask"] > df["bid"])].copy()
    flow["after_two_sided"] = len(df)
    df = df[(df["ask"] - df["bid"]) <= MAX_SPREAD + 1e-9].copy()
    flow["after_spread"] = len(df)

    df["mid"] = (df["bid"] + df["ask"]) / 2
    df["y"] = (df["result"] == "yes").astype(float)
    df["date"] = pd.to_datetime(df["close_ts"], unit="s", utc=True).dt.strftime("%Y-%m-%d")
    return df.drop(columns=["q"]), flow


def taker_fee(price: float, multiplier: object) -> float:
    """Return the Kalshi taker fee per contract, in dollars, on a 100-contract order."""
    m = Decimal(str(multiplier if multiplier is not None else 1))
    fee = kalshi_fee(Decimal(100), Decimal(str(round(price, 4))), True, multiplier=m)
    return float(fee) / 100


def rule_pnl(df: pd.DataFrame) -> pd.DataFrame:
    """Add net P&L per contract, after taker fees, for the fade and back rules.

    Fading buys No at 1 - yes_bid. Backing buys Yes at yes_ask.
    """
    df = df.copy()
    df["pnl_fade"] = [
        (1 - y) - (1 - b) - taker_fee(1 - b, m)
        for y, b, m in zip(df["y"], df["bid"], df["fee_multiplier"], strict=True)
    ]
    df["pnl_back"] = [
        y - a - taker_fee(a, m)
        for y, a, m in zip(df["y"], df["ask"], df["fee_multiplier"], strict=True)
    ]
    return df


def describe_rule(df: pd.DataFrame, column: str, mask: np.ndarray) -> dict:
    """Summarize a rule's P&L on the masked rows, with 97.5% intervals by event and date.

    The mean is None when the rows span fewer than two events.
    """
    sub = df[mask]
    if len(sub) == 0 or sub["event"].nunique() < 2:
        return {"n": int(len(sub)), "events": int(sub["event"].nunique()), "mean": None}
    values = sub[column].to_numpy()
    out = {
        "n": int(len(sub)),
        "events": int(sub["event"].nunique()),
        "dates": int(sub["date"].nunique()),
        "mean": float(values.mean()),
        "win_rate": float((values > 0).mean()),
    }
    for name, cluster in (("event", "event"), ("date", "date")):
        draws = cluster_bootstrap(lambda i, v=values: v[i].mean(), sub[cluster].to_numpy(), N_BOOT)
        out[f"ci975_{name}"] = interval(draws, 0.975)
    return out


def evaluate(df: pd.DataFrame) -> dict:
    """Return calibration, recalibration, rule, and per-category results for one split."""
    p, y = df["mid"].to_numpy(), df["y"].to_numpy()
    a, b = recalibration(p, y)

    def slope(i: np.ndarray) -> float:
        return recalibration(p[i], y[i], iterations=25)[1]

    result = {
        "n": int(len(df)),
        "events": int(df["event"].nunique()),
        "series": int(df["series"].nunique()),
        "dates": int(df["date"].nunique()),
        "first_close": df["close_time"].min(),
        "last_close": df["close_time"].max(),
        "yes_rate": float(y.mean()),
        "mean_spread": float((df["ask"] - df["bid"]).mean()),
        "brier": brier(p, y),
        "log_loss": log_loss(p, y),
        "ece": ece(p, y),
        "calibration_table": calibration_table(p, y),
        "recalibration": {
            "a": a,
            "b": b,
            "b_ci95_event": interval(cluster_bootstrap(slope, df["event"].to_numpy(), N_BOOT)),
            "b_ci95_date": interval(cluster_bootstrap(slope, df["date"].to_numpy(), N_BOOT)),
        },
        "fade_longshots": describe_rule(df, "pnl_fade", (df["mid"] <= 0.15).to_numpy()),
        "back_favorites": describe_rule(df, "pnl_back", (df["mid"] >= 0.85).to_numpy()),
        "by_category": {},
    }
    for category, sub in df.groupby("category"):
        if len(sub) >= 50 and 0 < sub["y"].mean() < 1:
            q, z = sub["mid"].to_numpy(), sub["y"].to_numpy()
            result["by_category"][category] = {
                "n": int(len(sub)),
                "events": int(sub["event"].nunique()),
                "brier": brier(q, z),
                "slope": recalibration(q, z)[1],
            }
    return result


def decide(rule: dict) -> dict:
    """Return whether a rule is supported: its event-clustered lower bound is above zero."""
    if rule.get("mean") is None:
        return {"supported": False, "note": "too few observations"}
    event_ok = rule["ci975_event"][0] > 0
    date_ok = rule["ci975_date"][0] > 0
    return {"supported": bool(event_ok), "robust_to_date_clustering": bool(event_ok and date_ok)}


def main() -> None:
    """Claim the holdout, evaluate the dev and holdout splits, and write results.json.

    Events are split 70/30 by their last close time. Both evaluations go to the trial
    ledger, and the ledger refuses a second holdout claim.
    """
    ledger.claim_holdout(EXPERIMENT)
    raw_bytes = RAW.read_bytes()
    digest = hashlib.sha256(raw_bytes).hexdigest()
    rows = [json.loads(line) for line in raw_bytes.decode().splitlines()]
    df, flow = build(rows)
    df = rule_pnl(df)

    order = df.groupby("event")["close_ts"].max().reset_index().sort_values(["close_ts", "event"])
    cut = int(len(order) * 0.7)
    dev_events = set(order["event"].iloc[:cut])
    dev, holdout = df[df["event"].isin(dev_events)], df[~df["event"].isin(dev_events)]

    dev_result = evaluate(dev)
    ledger.record_trial(
        EXPERIMENT,
        "dev",
        HERE / "preregistration.md",
        digest,
        {
            "n": dev_result["n"],
            "b": dev_result["recalibration"]["b"],
            "fade_mean": dev_result["fade_longshots"]["mean"],
            "back_mean": dev_result["back_favorites"]["mean"],
        },
    )
    hold_result = evaluate(holdout)
    b_low, b_high = hold_result["recalibration"]["b_ci95_event"]
    d_low, d_high = hold_result["recalibration"]["b_ci95_date"]
    decisions = {
        "H1": {
            "supported": bool(b_low > 1 or b_high < 1),
            "robust_to_date_clustering": bool(
                (b_low > 1 or b_high < 1) and (d_low > 1 or d_high < 1)
            ),
        },
        "H2a_fade_longshots": decide(hold_result["fade_longshots"]),
        "H2b_back_favorites": decide(hold_result["back_favorites"]),
    }
    ledger.record_trial(
        EXPERIMENT,
        "holdout",
        HERE / "preregistration.md",
        digest,
        {
            "n": hold_result["n"],
            "b": hold_result["recalibration"]["b"],
            "fade_mean": hold_result["fade_longshots"]["mean"],
            "back_mean": hold_result["back_favorites"]["mean"],
            "decisions": decisions,
        },
    )
    results = {
        "experiment": EXPERIMENT,
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset_sha256": digest,
        "prereg_sha256": ledger.file_sha256(HERE / "preregistration.md"),
        "sample_flow": flow,
        "dev": dev_result,
        "holdout": hold_result,
        "decisions": decisions,
    }
    (HERE / "results.json").write_text(json.dumps(results, indent=2, default=float) + "\n")
    print(json.dumps({"flow": flow, "decisions": decisions}, indent=2))


if __name__ == "__main__":
    main()

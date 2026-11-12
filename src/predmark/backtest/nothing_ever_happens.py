"""Backtest of Nothing Ever Happens on a panel built by `predmark.backtest.panel`.

Entry is the earliest hourly candle ending after `open_time`, within the window and strictly
before `close_time`, whose closing YES bid is above 0 and whose trailing 24-hour volume is at
least `min_volume`, the popularity rule the paper run applies to its live listing. NO is bought at
`1 - yes_bid` and held to settlement, paying the Kalshi taker fee on `lot` contracts. Uncertainty
resamples whole events.

This is exploratory. A claim about the strategy needs a preregistered experiment on new data.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..execution.fees import KALSHI_DIRECT_INCREMENT, kalshi_fee
from ..research.metrics import cluster_bootstrap, interval
from .panel import to_ts


def close_price(distribution: dict | None) -> float | None:
    """Live candles name this field close_dollars; archived candles name it close."""
    if not distribution:
        return None
    value = distribution.get("close_dollars", distribution.get("close"))
    return None if value is None else float(value)


def candle_volume(candle: dict) -> float:
    """Live candles name this field volume_fp; archived candles name it volume."""
    return float(candle.get("volume_fp", candle.get("volume")) or 0)


def entry(
    row: dict, window_hours: int = 24, min_volume: float = 0
) -> tuple[float, float | None] | None:
    """The earliest eligible candle with a bid, as (yes_bid, yes_ask). None if never tradable.

    Volume is summed over candles ending in the 24 hours up to and including the entry candle,
    so only trading that had already happened counts.
    """
    opened, closes = to_ts(row["open_time"]), to_ts(row["close_time"])
    candles = sorted(row["candles"], key=lambda c: c["end_period_ts"])
    for i, candle in enumerate(candles):
        end = candle["end_period_ts"]
        if not (opened < end <= opened + window_hours * 3600 and end < closes):
            continue
        if min_volume > 0:
            trailing = sum(
                candle_volume(c) for c in candles[: i + 1] if c["end_period_ts"] > end - 86400
            )
            if trailing < min_volume:
                continue
        bid = close_price(candle.get("yes_bid"))
        if bid is not None and bid > 0:
            return bid, close_price(candle.get("yes_ask"))
    return None


def fee_per_contract(price: float, multiplier: object, lot: int) -> float:
    """Return the Kalshi taker fee per contract on a lot at `price`, for a direct account.

    A missing `multiplier` counts as 1. The fee is rounded up on the whole lot, then divided.
    """
    m = Decimal(str(multiplier if multiplier is not None else 1))
    fee = kalshi_fee(
        Decimal(lot),
        Decimal(str(round(price, 4))),
        True,
        multiplier=m,
        increment=KALSHI_DIRECT_INCREMENT,
    )
    return float(fee) / lot


def trades(
    rows: list[dict], window_hours: int = 24, lot: int = 1, min_volume: float = 0
) -> pd.DataFrame:
    """Return one row per market with an entry: NO price, fee, and gross and net PnL per contract.

    `yes_net` is the control of buying YES at the ask. It is NaN when there is no ask below 1.
    """
    records = []
    for r in rows:
        found = entry(r, window_hours, min_volume)
        if found is None:
            continue
        bid, ask = found
        y = 1.0 if r["result"] == "yes" else 0.0
        no_price = 1 - bid
        fee = fee_per_contract(no_price, r.get("fee_multiplier"), lot)
        yes_ok = ask is not None and ask < 1
        records.append(
            {
                **{k: r.get(k) for k in ("series", "category", "event", "ticker")},
                "date": r["close_time"][:10],
                "y": y,
                "no_price": no_price,
                "fee": fee,
                "gross": (1 - y) - no_price,
                "net": (1 - y) - no_price - fee,
                "yes_net": (
                    y - ask - fee_per_contract(ask, r.get("fee_multiplier"), lot)
                    if yes_ok
                    else np.nan
                ),
            }
        )
    return pd.DataFrame(records)


def _ci(df: pd.DataFrame, column: str, n_boot: int) -> list[float] | None:
    if df["event"].nunique() < 2:
        return None
    values = df[column].to_numpy()
    draws = cluster_bootstrap(lambda i, v=values: v[i].mean(), df["event"].to_numpy(), n_boot)
    return list(interval(draws, 0.95))


def _group(df: pd.DataFrame) -> dict[str, Any]:
    return {
        "n": int(len(df)),
        "events": int(df["event"].nunique()),
        "mean_net": float(df["net"].mean()),
        "no_win_rate": float((df["y"] == 0).mean()),
        "mean_no_price": float(df["no_price"].mean()),
        "return_on_capital": float(df["net"].sum() / df["no_price"].sum()),
    }


def evaluate(
    rows: list[dict],
    window_hours: int = 24,
    lot: int = 1,
    n_boot: int = 2000,
    min_volume: float = 0,
) -> dict[str, Any]:
    """Return the summary of all trades, with intervals that resample whole events.

    Also breaks results down by category, NO price decile and series, plus a buy-YES control.
    Groups smaller than 50 markets by category, or 100 by series, are left out.
    """
    df = trades(rows, window_hours, lot, min_volume)
    out: dict[str, Any] = {
        "settings": {
            "window_hours": window_hours,
            "lot": lot,
            "n_boot": n_boot,
            "min_volume_24h": min_volume,
        },
        "candidates": len(rows),
        "request_errors": sum(1 for r in rows if r.get("error")),
        "traded": int(len(df)),
    }
    if df.empty:
        return out
    yes = df[df["yes_net"].notna()]
    out.update(
        {
            **_group(df),
            "series": int(df["series"].nunique()),
            "first_close": df["date"].min(),
            "last_close": df["date"].max(),
            "mean_gross": float(df["gross"].mean()),
            "mean_fee": float(df["fee"].mean()),
            "total_net": float(df["net"].sum()),
            "net_ci95_event": _ci(df, "net", n_boot),
            "control_buy_yes": {
                "n": int(len(yes)),
                "mean_net": float(yes["yes_net"].mean()) if len(yes) else None,
                "ci95_event": _ci(yes, "yes_net", n_boot) if len(yes) else None,
            },
            "by_category": {
                str(k): _group(v) for k, v in df.groupby("category", dropna=False) if len(v) >= 50
            },
            "by_no_price": {},
            "by_series": {str(k): _group(v) for k, v in df.groupby("series") if len(v) >= 100},
        }
    )
    bins = np.minimum((df["no_price"].to_numpy() * 10).astype(int), 9)
    for b in sorted(set(bins)):
        out["by_no_price"][f"{b / 10:.1f}-{(b + 1) / 10:.1f}"] = _group(df[bins == b])
    return out


def run(
    panel: str | Path, window_hours: int = 24, lot: int = 1, min_volume: float = 0
) -> dict[str, Any]:
    """Read a panel's `raw.jsonl` and return its evaluation, with the panel path."""
    rows = [json.loads(line) for line in Path(panel).read_text().splitlines() if line.strip()]
    return {"panel": str(panel), **evaluate(rows, window_hours, lot, min_volume=min_volume)}

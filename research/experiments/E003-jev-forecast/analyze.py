"""Evaluate E003 exactly as preregistered. Writes results.json beside this file.

    uv run python research/experiments/E003-jev-forecast/analyze.py dev
    uv run python research/experiments/E003-jev-forecast/analyze.py holdout  # once, from CUTOFF

Reads data/e003/ as written by fetch.py. Dev may be run freely and every run is logged in the trial
ledger. The holdout is refused before CUTOFF and after its first evaluation.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

from predmark.collect.jev_forecasts import _price, decide
from predmark.execution.fees import kalshi_fee
from predmark.research import ledger
from predmark.research.metrics import (
    brier,
    calibration_table,
    cluster_bootstrap,
    interval,
    log_loss,
    logit,
)

HERE = Path(__file__).parent
DATA = Path("data/e003")
EXPERIMENT = "E003"
WINDOWS = {"dev": ("2026-11-08", "2026-11-21"), "holdout": ("2026-11-22", "2027-02-21")}
CUTOFF = "2027-03-07"
MIN_BETS = 100
N_BOOT = 2000
MARGINS = (Decimal("0"), Decimal("0.02"), Decimal("0.05"), Decimal("0.10"))


def split_of(ts: str) -> str | None:
    day = ts[:10]
    return next((s for s, (lo, hi) in WINDOWS.items() if lo <= day <= hi), None)


def settled_by_cutoff(outcome: dict) -> bool:
    """A yes or no result whose settlement time is on or before the cutoff date (UTC)."""
    if outcome.get("result") not in {"yes", "no"} or not outcome.get("settlement_ts"):
        return False
    return outcome["settlement_ts"][:10] <= CUTOFF


def fee(price: float, multiplier: float = 1.0) -> float:
    """The Kalshi taker fee on one contract, rounded up to the cent."""
    return float(kalshi_fee(Decimal(1), Decimal(str(price)), multiplier=Decimal(str(multiplier))))


def bet_net(bet: str, y: float, bid: float, ask: float, multiplier: float = 1.0) -> float:
    if bet == "yes":
        return y - ask - fee(ask, multiplier)
    return (1 - y) - (1 - bid) - fee(bid, multiplier)


def build(
    forecasts: list[dict], outcomes: dict[str, dict], series: dict[str, dict], split: str
) -> tuple[pd.DataFrame, dict]:
    """One row per forecast in `split`. `y` is NaN unless it settled yes or no by the cutoff."""
    flow: Counter = Counter()
    records = []
    for r in forecasts:
        if split_of(r["ts"]) != split:
            continue
        flow["forecasts"] += 1
        if "error" in r:
            flow["jev_errors"] += 1
            continue
        q = r["quote"]
        bid, ask = _price(q.get("yes_bid_dollars")), _price(q.get("yes_ask_dollars"))
        p = float(r["p_yes"])
        stored = r["decision"]["bet"]
        # Recomputed from the recorded inputs, to catch a collector that drifted from the prereg.
        if decide(Decimal(str(r["p_yes"])), bid, ask)["bet"] != stored:
            flow["decision_mismatch"] += 1
        outcome = outcomes.get(r["ticker"], {})
        resolved = settled_by_cutoff(outcome)
        if not resolved:
            flow["unresolved"] += 1
        event = r.get("event_ticker") or r["ticker"]
        s = series.get(event.split("-", 1)[0], {})
        records.append(
            {
                "ticker": r["ticker"],
                "event": event,
                "series": event.split("-", 1)[0],
                "category": s.get("category") or "unknown",
                "fee_multiplier": float(s.get("fee_multiplier") or 1),
                "model": r.get("model"),
                "p": p,
                "bid": float(bid) if bid is not None else np.nan,
                "ask": float(ask) if ask is not None else np.nan,
                "bet": stored,
                "y": (1.0 if outcome["result"] == "yes" else 0.0) if resolved else np.nan,
            }
        )
    df = pd.DataFrame(records)
    if len(df):
        df["mid"] = (df["bid"] + df["ask"]) / 2
    flow["resolved"] = int(df["y"].notna().sum()) if len(df) else 0
    return df, dict(flow)


def with_net(bets: pd.DataFrame) -> pd.DataFrame:
    out = bets.copy()
    rows = out.itertuples()
    out["net"] = [bet_net(b.bet, b.y, b.bid, b.ask) for b in rows]
    out["net_series_fee"] = [
        bet_net(b.bet, b.y, b.bid, b.ask, b.fee_multiplier) for b in out.itertuples()
    ]
    out["price"] = np.where(out["bet"] == "yes", out["ask"], out["bid"])
    return out


def logistic(X: np.ndarray, y: np.ndarray, iterations: int = 50) -> np.ndarray:
    beta = np.zeros(X.shape[1])
    for _ in range(iterations):
        mu = 1 / (1 + np.exp(-np.clip(X @ beta, -30, 30)))
        w = np.clip(mu * (1 - mu), 1e-9, None)
        hessian = (X * w[:, None]).T @ X + 1e-9 * np.eye(X.shape[1])
        step = np.linalg.solve(hessian, X.T @ (y - mu))
        beta = beta + step
        if np.max(np.abs(step)) < 1e-8:
            break
    return beta


def mean_ci(df: pd.DataFrame, column: str, cluster: str) -> tuple[float, float]:
    values = df[column].to_numpy()
    draws = cluster_bootstrap(lambda i: values[i].mean(), df[cluster].to_numpy(), N_BOOT)
    return interval(draws)


def h1(bets: pd.DataFrame) -> dict:
    out: dict = {
        "n_bets": int(len(bets)),
        "events": int(bets["event"].nunique()) if len(bets) else 0,
    }
    if len(bets) < 2 or bets["event"].nunique() < 2:
        return {**out, "supported": False, "reason": "too few bets to estimate"}
    out["mean_net"] = float(bets["net"].mean())
    out["net_ci95"] = {c: mean_ci(bets, "net", c) for c in ("event", "series")}
    out["mean_net_series_fee"] = float(bets["net_series_fee"].mean())
    event_low, series_low = out["net_ci95"]["event"][0], out["net_ci95"]["series"][0]
    powered = len(bets) >= MIN_BETS
    out["supported"] = bool(powered and event_low > 0)
    out["robust_to_series_clustering"] = bool(out["supported"] and series_low > 0)
    if not powered:
        out["reason"] = f"fewer than {MIN_BETS} settled bets"
    return out


def h2(resolved: pd.DataFrame) -> dict:
    sub = resolved.dropna(subset=["mid"])
    out: dict = {"n": int(len(sub)), "events": int(sub["event"].nunique()) if len(sub) else 0}
    if len(sub) < 10 or sub["event"].nunique() < 2:
        return {**out, "supported": False, "reason": "too few forecasts with a two-sided quote"}
    X = np.column_stack(
        [np.ones(len(sub)), logit(sub["mid"].to_numpy()), logit(sub["p"].to_numpy())]
    )
    y = sub["y"].to_numpy()
    beta = logistic(X, y)
    out["coef"] = {
        "intercept": float(beta[0]),
        "logit_mid": float(beta[1]),
        "logit_p": float(beta[2]),
    }
    out["logit_p_ci95"] = {
        c: interval(cluster_bootstrap(lambda i: logistic(X[i], y[i])[2], sub[c].to_numpy(), N_BOOT))
        for c in ("event", "series")
    }
    out["supported"] = bool(out["logit_p_ci95"]["event"][0] > 0)
    out["robust_to_series_clustering"] = bool(
        out["supported"] and out["logit_p_ci95"]["series"][0] > 0
    )
    return out


def group(bets: pd.DataFrame) -> dict:
    return {"n": int(len(bets)), "mean_net": float(bets["net"].mean()) if len(bets) else None}


def calibration(p: np.ndarray, y: np.ndarray) -> dict:
    return {
        "brier": brier(p, y),
        "log_loss": log_loss(p, y),
        "table": calibration_table(p, y),
    }


def at_margin(resolved: pd.DataFrame, margin: Decimal) -> pd.DataFrame:
    """The bets the rule would take at another margin, from the same recorded inputs."""
    sides = []
    for r in resolved.itertuples():
        d = decide(
            Decimal(str(r.p)),
            None if np.isnan(r.bid) else Decimal(str(r.bid)),
            None if np.isnan(r.ask) else Decimal(str(r.ask)),
        )
        best = max(
            (Decimal(e) for e in (d["yes_edge"], d["no_edge"]) if e is not None), default=None
        )
        if best is None or best < margin:
            sides.append(None)
        else:
            sides.append(
                "yes" if d["yes_edge"] is not None and Decimal(d["yes_edge"]) == best else "no"
            )
    out = resolved.assign(bet=sides)
    return with_net(out[out["bet"].notna()])


def evaluate(df: pd.DataFrame, flow: dict, spend_usd: float | None = None) -> dict:
    resolved = df[df["y"].notna()]
    bets = with_net(resolved[resolved["bet"].notna()])
    quoted = resolved.dropna(subset=["mid"])
    result = {
        "flow": flow,
        "models": dict(Counter(df["model"].fillna("none"))) if len(df) else {},
        "bet_rate": float(df["bet"].notna().mean()) if len(df) else None,
        "jev_error_rate": flow.get("jev_errors", 0) / max(flow.get("forecasts", 0), 1),
        "jev_spend_usd": spend_usd,
        "H1": h1(bets),
        "H2": h2(resolved),
        "calibration": {
            "jev": calibration(quoted["p"].to_numpy(), quoted["y"].to_numpy()),
            "mid": calibration(quoted["mid"].to_numpy(), quoted["y"].to_numpy()),
            "n": int(len(quoted)),
        }
        if len(quoted)
        else None,
        "by_side": {str(k): group(v) for k, v in bets.groupby("bet")},
        "by_category": {str(k): group(v) for k, v in bets.groupby("category")},
        "by_entry_price": {},
        "by_margin": {},
    }
    if len(bets):
        buckets = np.minimum((bets["price"].to_numpy() * 10).astype(int), 9)
        for b in sorted(set(buckets)):
            result["by_entry_price"][f"{b / 10:.1f}-{(b + 1) / 10:.1f}"] = group(bets[buckets == b])
    for m in MARGINS:
        sub = at_margin(resolved, m)
        entry = group(sub)
        if len(sub) > 1 and sub["event"].nunique() > 1:
            entry["net_ci95_event"] = mean_ci(sub, "net", "event")
        result["by_margin"][str(m)] = entry
    return result


def read_jsonl(path: Path) -> list[dict]:
    return (
        [json.loads(x) for x in path.read_text().splitlines() if x.strip()] if path.exists() else []
    )


def spend(cost: list[dict], split: str) -> float:
    return float(sum(Decimal(c.get("cost_usd", "0")) for c in cost if split_of(c["ts"]) == split))


def main(argv: list[str] | None = None, today: date | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("split", choices=("dev", "holdout"))
    args = parser.parse_args(argv)
    today = today or datetime.now(UTC).date()
    if args.split == "holdout":
        if today < date.fromisoformat(CUTOFF):
            raise SystemExit(f"the holdout may not be evaluated before {CUTOFF}")
        ledger.claim_holdout(EXPERIMENT)

    raw = (DATA / "forecasts.jsonl").read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    forecasts = [json.loads(x) for x in raw.decode().splitlines() if x.strip()]
    outcomes = {o["ticker"]: o for o in read_jsonl(DATA / f"outcomes_{args.split}.jsonl")}
    series = {s["ticker"]: s for s in read_jsonl(DATA / "series.jsonl")}
    df, flow = build(forecasts, outcomes, series, args.split)
    result = evaluate(df, flow, spend(read_jsonl(DATA / "cost.jsonl"), args.split))
    decisions = {
        k: {
            "supported": result[k]["supported"],
            "robust_to_series_clustering": result[k].get("robust_to_series_clustering", False),
        }
        for k in ("H1", "H2")
    }
    prereg = HERE / "preregistration.md"
    ledger.record_trial(
        EXPERIMENT,
        args.split,
        prereg,
        digest,
        {
            "n_bets": result["H1"]["n_bets"],
            "mean_net": result["H1"].get("mean_net"),
            **({"decisions": decisions} if args.split == "holdout" else {}),
        },
    )
    path = HERE / "results.json"
    results = json.loads(path.read_text()) if path.exists() else {"experiment": EXPERIMENT}
    results.update(
        {
            "generated_at": datetime.now(UTC).isoformat(),
            "prereg_sha256": ledger.file_sha256(prereg),
            args.split: {"dataset_sha256": digest, **result},
        }
    )
    if args.split == "holdout":
        results["decisions"] = decisions
    path.write_text(json.dumps(results, indent=2, default=float) + "\n")
    print(json.dumps({"split": args.split, "flow": flow, "decisions": decisions}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

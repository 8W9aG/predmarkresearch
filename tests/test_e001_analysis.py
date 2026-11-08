"""Exercise the E001 pipeline on synthetic data, so a bug cannot surface after the holdout opens."""

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

SPEC = importlib.util.spec_from_file_location(
    "e001_analyze", Path("research/experiments/E001-executable-calibration/analyze.py")
)
analyze = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(analyze)


def market(i, event, close, bid, ask, result, series="S1", stale=0, error=None):
    close_ts = int(close.timestamp())
    candle = {
        "end_period_ts": close_ts - analyze.HORIZON - stale,
        "yes_bid": {"close_dollars": f"{bid:.4f}"},
        "yes_ask": {"close_dollars": f"{ask:.4f}"},
    }
    return {
        "series": series,
        "category": "Economics",
        "fee_multiplier": 1,
        "event": event,
        "ticker": f"{event}-{i}",
        "close_time": close.isoformat().replace("+00:00", "Z"),
        "result": result,
        "candles": [candle],
        "error": error,
    }


BASE = datetime(2026, 1, 1, 17, tzinfo=UTC)


def test_filters_remove_ineligible_markets():
    rows = [market(i, f"E{i}", BASE + timedelta(days=i), 0.40, 0.45, "yes") for i in range(6)]
    rows.append(market(90, "EARLY", BASE + timedelta(days=9, hours=6), 0.40, 0.45, "yes"))
    rows.append(market(91, "ONESIDED", BASE + timedelta(days=9), 0.0, 0.45, "yes"))
    rows.append(market(92, "NOASK", BASE + timedelta(days=9), 0.40, 1.0, "yes"))
    rows.append(market(93, "WIDE", BASE + timedelta(days=9), 0.30, 0.45, "yes"))
    rows.append(market(94, "STALE", BASE + timedelta(days=9), 0.40, 0.45, "yes", stale=4 * 3600))
    rows.append(market(95, "RARE", BASE, 0.40, 0.45, "yes", series="TINY"))
    df, flow = analyze.build(rows)
    assert set(df["event"]) == {f"E{i}" for i in range(6)}
    assert flow["candidates"] == 12
    assert flow["after_min_series"] == 11
    assert flow["after_scheduled_close"] == 10


def test_candle_after_observation_time_is_never_used():
    row = market(0, "E", BASE, 0.40, 0.45, "yes")
    row["candles"][0]["end_period_ts"] += 1
    assert analyze.quote(row) is None


def test_quote_reads_archived_candle_field_names():
    row = market(0, "E", BASE, 0.40, 0.45, "yes")
    row["candles"][0]["yes_bid"] = {"close": "0.4000"}
    row["candles"][0]["yes_ask"] = {"close": "0.4500"}
    assert analyze.quote(row) == (0.40, 0.45)


def test_rule_pnl_arithmetic():
    rows = [market(i, f"E{i}", BASE + timedelta(days=i), 0.10, 0.12, "no") for i in range(5)]
    rows += [market(9, "F", BASE, 0.90, 0.92, "yes")]
    df = analyze.rule_pnl(analyze.build(rows)[0])
    fade = df[df["event"] == "E0"]["pnl_fade"].iloc[0]
    # Buy NO at 0.90 wins 0.10, less 0.07 * 100 * .9 * .1 = 0.63 per 100 contracts.
    assert fade == pytest.approx(1 - 0.90 - 0.0063)
    back = df[df["event"] == "F"]["pnl_back"].iloc[0]
    assert back == pytest.approx(1 - 0.92 - 0.0052)


def synthetic(slope, seed=0, events=1200):
    rng = np.random.default_rng(seed)
    rows = []
    for e in range(events):
        close = BASE + timedelta(days=int(e / 5))
        logits = rng.normal(0, 1.4, 1)[0]
        mid = float(np.clip(1 / (1 + np.exp(-logits)), 0.03, 0.97))
        truth = 1 / (1 + np.exp(-slope * np.log(mid / (1 - mid))))
        result = "yes" if rng.random() < truth else "no"
        bid, ask = round(mid - 0.01, 2), round(mid + 0.01, 2)
        rows.append(market(0, f"E{e:05d}", close, bid, ask, result))
    return rows


def test_pipeline_detects_planted_miscalibration(monkeypatch):
    monkeypatch.setattr(analyze, "N_BOOT", 100)
    df = analyze.rule_pnl(analyze.build(synthetic(1.6))[0])
    result = analyze.evaluate(df)
    assert result["recalibration"]["b_ci95_event"][0] > 1


def test_pipeline_finds_no_slope_or_edge_when_prices_are_right(monkeypatch):
    monkeypatch.setattr(analyze, "N_BOOT", 100)
    df = analyze.rule_pnl(analyze.build(synthetic(1.0, seed=3))[0])
    result = analyze.evaluate(df)
    low, high = result["recalibration"]["b_ci95_event"]
    assert low < 1 < high
    assert analyze.decide(result["fade_longshots"])["supported"] is False
    assert analyze.decide(result["back_favorites"])["supported"] is False

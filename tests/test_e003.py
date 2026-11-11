"""E003 analysis on synthetic data: a planted edge is found, a null is not, nothing after the
cutoff is used, and the holdout is guarded."""

import importlib.util
import json
import sys
from datetime import date
from decimal import Decimal

import numpy as np
import pytest

from predmark.collect.jev_forecasts import decide
from predmark.research import ledger

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1] / "research/experiments"
HERE = ROOT / "E003-jev-forecast"
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location("e003_analyze", HERE / "analyze.py")
analyze = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analyze)
analyze.N_BOOT = 200


def forecast(i, p, bid, ask, ts="2026-11-25T14:00:00", event=None):
    d = decide(Decimal(str(p)), Decimal(str(bid)), Decimal(str(ask)))
    return {
        "ts": ts,
        "ticker": f"T{i}",
        "event_ticker": event or f"S{i % 5}-E{i // 2}",
        "quote": {"yes_bid_dollars": f"{bid:.2f}", "yes_ask_dollars": f"{ask:.2f}"},
        "model": "jev-1.14.0",
        "p_yes": p,
        "decision": d,
    }


def synthetic(informative: bool, n=1600, seed=0):
    """True probability q. Planted: the market misprices q by 25 cents and Jev knows q. Null: the
    market prices q correctly and Jev is uniform noise, so every bet pays spread and fee."""
    rng = np.random.default_rng(seed)
    rows, outcomes = [], {}
    for i in range(n):
        q = rng.uniform(0.1, 0.9)
        error = rng.choice([-0.25, 0.25]) if informative else 0.0
        mid = float(np.clip(q + error, 0.05, 0.95))
        bid, ask = round(mid - 0.01, 2), round(mid + 0.01, 2)
        p = q if informative else float(rng.uniform(0.05, 0.95))
        rows.append(forecast(i, round(p, 4), bid, ask))
        y = rng.random() < q
        outcomes[f"T{i}"] = {
            "ticker": f"T{i}",
            "result": "yes" if y else "no",
            "settlement_ts": "2026-12-01T00:00:00Z",
        }
    return rows, outcomes


def run(rows, outcomes, split="holdout"):
    df, flow = analyze.build(rows, outcomes, {}, split)
    return analyze.evaluate(df, flow)


def test_planted_edge_is_found():
    r = run(*synthetic(informative=True))
    assert r["flow"].get("decision_mismatch", 0) == 0
    assert r["H1"]["supported"] and r["H1"]["mean_net"] > 0.1
    assert r["H2"]["supported"] and r["H2"]["coef"]["logit_p"] > 0.5
    assert r["calibration"]["jev"]["brier"] < r["calibration"]["mid"]["brier"]


def test_null_is_not_found():
    r = run(*synthetic(informative=False, seed=1))
    assert not r["H1"]["supported"] and r["H1"]["mean_net"] < 0
    assert not r["H2"]["supported"]


def test_too_few_bets_is_not_supported():
    rows, outcomes = synthetic(informative=True, n=60)
    r = run(rows, outcomes)
    assert r["H1"]["n_bets"] < analyze.MIN_BETS
    assert not r["H1"]["supported"] and "fewer than" in r["H1"]["reason"]


def test_nothing_after_the_cutoff_or_outside_the_split_is_used():
    rows = [
        forecast(0, 0.9, 0.3, 0.32),
        forecast(1, 0.9, 0.3, 0.32),
        forecast(2, 0.9, 0.3, 0.32),
        forecast(3, 0.9, 0.3, 0.32, ts="2026-11-20T14:00:00"),  # dev
        forecast(4, 0.9, 0.3, 0.32, ts="2027-02-22T14:00:00"),  # after the window
    ]
    outcomes = {
        "T0": {"ticker": "T0", "result": "yes", "settlement_ts": "2027-03-07T23:00:00Z"},
        "T1": {"ticker": "T1", "result": "yes", "settlement_ts": "2027-03-08T00:00:00Z"},
        "T2": {"ticker": "T2", "result": "", "settlement_ts": None},  # voided or open
        "T3": {"ticker": "T3", "result": "yes", "settlement_ts": "2026-11-21T00:00:00Z"},
    }
    df, flow = analyze.build(rows, outcomes, {}, "holdout")
    assert list(df["ticker"]) == ["T0", "T1", "T2"]
    assert df["y"].notna().tolist() == [True, False, False]
    assert flow == {"forecasts": 3, "unresolved": 2, "resolved": 1}


def test_jev_errors_and_drifted_decisions_are_counted():
    good = forecast(0, 0.9, 0.3, 0.32)
    drifted = forecast(1, 0.9, 0.3, 0.32)
    drifted["decision"] = {**drifted["decision"], "bet": None}
    error = {"ts": "2026-11-25T14:00:00", "ticker": "T2", "error": "JevError: 400"}
    _, flow = analyze.build([good, drifted, error], {}, {}, "holdout")
    assert flow["jev_errors"] == 1 and flow["decision_mismatch"] == 1


def test_net_uses_the_recorded_quote_and_one_contract_fee():
    assert analyze.bet_net("yes", 1.0, 0.30, 0.32) == pytest.approx(1 - 0.32 - 0.02)
    assert analyze.bet_net("no", 1.0, 0.30, 0.32) == pytest.approx(0 - 0.70 - 0.02)
    assert analyze.bet_net("no", 0.0, 0.30, 0.32) == pytest.approx(1 - 0.70 - 0.02)


def test_margin_table_includes_the_preregistered_margin():
    rows, outcomes = synthetic(informative=True, n=400)
    r = run(rows, outcomes)
    assert r["by_margin"]["0.05"]["n"] == r["H1"]["n_bets"]
    assert r["by_margin"]["0"]["n"] >= r["by_margin"]["0.05"]["n"] >= r["by_margin"]["0.10"]["n"]


def test_holdout_is_refused_before_the_cutoff_and_after_its_first_look(tmp_path, monkeypatch):
    with pytest.raises(SystemExit, match="may not be evaluated before"):
        analyze.main(["holdout"], today=date(2027, 3, 6))
    trials = tmp_path / "trials.jsonl"
    trials.write_text(json.dumps({"experiment": "E003", "split": "holdout"}) + "\n")
    claim = ledger.claim_holdout
    monkeypatch.setattr(analyze.ledger, "claim_holdout", lambda e: claim(e, trials))
    with pytest.raises(ledger.HoldoutAlreadyUsed):
        analyze.main(["holdout"], today=date(2027, 3, 7))


def test_fetch_refuses_holdout_outcomes_before_the_cutoff():
    spec = importlib.util.spec_from_file_location("e003_fetch", HERE / "fetch.py")
    fetch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fetch)
    with pytest.raises(SystemExit, match="may not be pulled before"):
        fetch.main(["holdout"], today=date(2027, 3, 6))
    assert fetch.series_of("KXNFLGAME-26NOV08NYJKC") == "KXNFLGAME"

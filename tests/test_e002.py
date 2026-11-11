"""E002 analysis on synthetic data, and the paper strategy, before the holdout opens."""

import importlib.util
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path

import numpy as np
import pytest

from predmark.execution.engine import Engine
from predmark.execution.models import Book, OrderStatus
from predmark.execution.paper import PaperVenue
from predmark.execution.risk import RiskLimits
from predmark.strategies.nothing_ever_happens import run_pass

ROOT = Path("research/experiments/E002-nothing-ever-happens")


def load(name):
    spec = importlib.util.spec_from_file_location(f"e002_{name}", ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


analyze = load("analyze")
panel = load("build_panel")
BASE = datetime(2026, 1, 1, tzinfo=UTC)


def iso(t):
    return t.isoformat().replace("+00:00", "Z")


def row(event="E", ticker="T", bid=0.20, ask=0.25, result="no", offset=3600, life=86400 * 3, **kw):
    opened = BASE
    candle = {
        "end_period_ts": int(opened.timestamp()) + offset,
        "yes_bid": {"close_dollars": f"{bid:.4f}"},
        "yes_ask": {"close_dollars": f"{ask:.4f}"},
    }
    return {
        "series": kw.get("series", "S"),
        "category": "Sports",
        "fee_multiplier": kw.get("fee_multiplier", 1),
        "event": event,
        "ticker": ticker,
        "split": kw.get("split", "dev"),
        "event_size": 1,
        "open_time": iso(opened),
        "close_time": iso(opened + timedelta(seconds=life)),
        "result": result,
        "candles": [candle],
        "error": None,
    }


def test_net_arithmetic_for_a_winning_and_losing_no():
    df, _ = analyze.build([row(ticker="W"), row(ticker="L", result="yes")])
    win, lose = df[df["ticker"] == "W"].iloc[0], df[df["ticker"] == "L"].iloc[0]
    # NO costs 0.80; fee is roundup(0.07 * 100 * .8 * .2) = 1.12 per 100 contracts.
    assert win["net"] == pytest.approx(0.20 - 0.0112)
    assert lose["net"] == pytest.approx(-0.80 - 0.0112)
    assert win["gross"] == pytest.approx(0.20)


def test_entry_requires_a_bid():
    df, flow = analyze.build([row(ticker="A"), row(ticker="B", bid=0.0)])
    assert set(df["ticker"]) == {"A"}
    assert flow["never_tradable"] == 1


def test_entry_skips_candles_outside_the_window():
    before = row(offset=0)
    at_close = row(offset=3600, life=3600)
    late = row(offset=analyze.WINDOW + 1, life=86400 * 3)
    assert analyze.entry(before) is None
    assert analyze.entry(at_close) is None
    assert analyze.entry(late) is None


def test_entry_takes_the_earliest_bid_and_reads_archived_names():
    r = row(offset=7200, bid=0.5, ask=0.6)
    r["candles"].insert(
        0,
        {
            "end_period_ts": r["candles"][0]["end_period_ts"] - 3600,
            "yes_bid": {"close": "0.1000"},
            "yes_ask": {"close": "0.1500"},
        },
    )
    assert analyze.entry(r) == (0.10, 0.15)


def test_sampling_keeps_events_whole_and_respects_budgets():
    markets = []
    for e in range(200):
        close = "2026-11-02T00:00:00Z" if e % 2 else "2026-01-02T00:00:00Z"
        for m in range(3):
            markets.append({"event": f"E{e}", "ticker": f"E{e}-{m}", "close_time": close})
    chosen = panel.sample_events(markets, {"dev": 30, "holdout": 15})
    sizes = {}
    for c in chosen:
        sizes.setdefault(c["event"], []).append(c)
        assert c["event_size"] == 3
    assert all(len(v) == 3 for v in sizes.values())
    assert sum(c["split"] == "dev" for c in chosen) == 30
    assert sum(c["split"] == "holdout" for c in chosen) == 15


def test_event_spanning_the_boundary_goes_to_holdout():
    markets = [
        {"event": "E", "ticker": "a", "close_time": "2026-10-31T00:00:00Z"},
        {"event": "E", "ticker": "b", "close_time": "2026-11-02T00:00:00Z"},
    ]
    assert {c["split"] for c in panel.sample_events(markets)} == {"holdout"}


def synthetic(no_edge, seed=0, events=1500):
    rng = np.random.default_rng(seed)
    rows = []
    for e in range(events):
        bid = float(rng.uniform(0.05, 0.60))
        truth = max(0.0, bid - no_edge)  # lower true YES rate gives NO an edge
        result = "yes" if rng.random() < truth else "no"
        rows.append(
            row(f"E{e}", f"T{e}", round(bid, 2), round(bid + 0.02, 2), result, series=f"S{e % 40}")
        )
    return analyze.build(rows)[0]


def test_detects_a_planted_edge(monkeypatch):
    monkeypatch.setattr(analyze, "N_BOOT", 100)
    result = analyze.evaluate(synthetic(0.15), ("event", "series"))
    assert analyze.decide(result)["supported"] is True
    assert result["mean_net"] > 0


def test_fair_prices_lose_the_fee(monkeypatch):
    monkeypatch.setattr(analyze, "N_BOOT", 100)
    result = analyze.evaluate(synthetic(0.0, seed=4), ("event", "series"))
    assert analyze.decide(result)["supported"] is False
    assert result["mean_net"] < 0


# paper strategy ------------------------------------------------------------------------------


class Exchange:
    def __init__(self):
        self.now = 1000.0
        self.results = {}

    def clock(self):
        return self.now

    def book(self, ticker):
        if ticker == "NOBID":
            return Book(ticker, (), ((D("0.50"), D(10)),), self.now)
        return Book(ticker, ((D("0.20"), D(100)),), ((D("0.25"), D(100)),), self.now)

    def market(self, ticker):
        return {"status": "active", "result": self.results.get(ticker, "")}

    def lookup(self, tickers):
        return {t: self.market(t) for t in tickers}


@pytest.fixture
def rig(tmp_path):
    ex = Exchange()
    venue = PaperVenue(ex.book, cash=D(100), clock=ex.clock)
    engine = Engine(venue, tmp_path / "j.sqlite", kill_file=tmp_path / "K", clock=ex.clock)
    return ex, venue, engine, tmp_path


def m(*tickers):
    return [{"ticker": t} for t in tickers]


def test_strategy_buys_no_by_selling_yes_at_the_bid(rig):
    ex, venue, engine, _ = rig
    summary = run_pass(engine, m("A", "NOBID"), ex.lookup)
    assert summary.entered == 1 and summary.no_bid == 1
    assert engine.ledger.contracts("A") == D(-1)
    assert venue.positions() == {"A": D(-1)}
    assert engine.reconcile() == []


def test_strategy_does_not_enter_a_market_twice(rig):
    ex, _, engine, _ = rig
    run_pass(engine, m("A"), ex.lookup)
    assert run_pass(engine, m("A"), ex.lookup).skipped == 1
    assert engine.ledger.contracts("A") == D(-1)


def test_no_wins_when_market_resolves_no(rig):
    ex, venue, engine, _ = rig
    run_pass(engine, m("A"), ex.lookup)  # receives 0.20 for selling YES, pays a 0.0112 fee
    ex.results["A"] = "no"
    assert run_pass(engine, [], ex.lookup).settled == 1
    assert engine.ledger.realized == D("0.1888")
    assert venue.balance() == D("100.1888")
    assert engine.reconcile() == []


def test_no_loses_when_market_resolves_yes(rig):
    ex, venue, engine, _ = rig
    run_pass(engine, m("A"), ex.lookup)
    ex.results["A"] = "yes"
    run_pass(engine, [], ex.lookup)
    assert engine.ledger.realized == D("-0.8112")
    assert venue.balance() == D("99.1888")


def test_strategy_stops_at_the_risk_limit(rig):
    ex, venue, engine, _ = rig
    engine.limits = RiskLimits(max_total_loss=D("1.70"))  # each NO risks about 0.82
    summary = run_pass(engine, m(*"ABCDEF"), ex.lookup)
    assert summary.entered == 2
    assert "total worst-case loss" in summary.stopped
    assert len(venue.positions()) == 2


def test_restart_restores_paper_state(rig):
    ex, venue, engine, tmp = rig
    run_pass(engine, m("A", "B"), ex.lookup)
    ex.results["B"] = "no"
    run_pass(engine, [], ex.lookup)
    balance = venue.balance()
    fresh = PaperVenue(ex.book, cash=D(100), clock=ex.clock)
    again = Engine(fresh, tmp / "j.sqlite", kill_file=tmp / "K", clock=ex.clock)
    fresh.restore(again.ledger, D(100))
    assert fresh.balance() == balance
    assert fresh.positions() == {"A": D(-1)}
    assert again.reconcile() == []
    assert run_pass(again, m("A"), ex.lookup).skipped == 1


def test_strategy_only_uses_ioc_orders(rig):
    ex, venue, engine, _ = rig
    run_pass(engine, m("A"), ex.lookup)
    assert venue.open_orders() == []
    row_ = engine.db.execute("SELECT tif, status FROM orders").fetchone()
    assert row_["tif"] == "immediate_or_cancel" and row_["status"] == OrderStatus.FILLED.value

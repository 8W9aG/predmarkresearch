"""Backtest on synthetic panels with a planted effect, the CLI, and the scaled paper runner."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import httpx
import numpy as np

from predmark.backtest import nothing_ever_happens as bt
from predmark.backtest.panel import sample_events
from predmark.cli import main
from predmark.data.kalshi import KalshiData
from predmark.execution.engine import Engine
from predmark.execution.models import Book, Fill, OrderRequest, Side, TimeInForce
from predmark.execution.paper import PaperVenue
from predmark.execution.risk import Ledger, RiskLimits, check_order
from predmark.http import ReadClient
from predmark.strategies.base import PAPER_LIMITS, CachedBooks, listing_book, report
from predmark.strategies.nothing_ever_happens import run_pass

BASE = datetime(2026, 1, 1, tzinfo=UTC)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def candle(end, bid, ask, archived=False):
    key = "close" if archived else "close_dollars"
    return {
        "end_period_ts": int(end.timestamp()),
        "yes_bid": {key: f"{bid:.4f}"},
        "yes_ask": {key: f"{ask:.4f}"},
    }


def market(i, bid, result, hours=48, candles=None, event=None):
    opened = BASE + timedelta(days=i)
    return {
        "series": f"S{i % 7}",
        "category": "Sports",
        "event": event or f"E{i // 3}",
        "ticker": f"M{i}",
        "fee_multiplier": 1,
        "open_time": iso(opened),
        "close_time": iso(opened + timedelta(hours=hours)),
        "result": result,
        "candles": candles
        if candles is not None
        else [candle(opened + timedelta(hours=1), bid, bid + 0.02, archived=i % 2 == 0)],
    }


def panel(no_rate, n=600, bid=0.30, seed=0):
    rng = np.random.default_rng(seed)
    return [market(i, bid, "no" if rng.random() < no_rate else "yes", 48) for i in range(n)]


def test_planted_edge_is_found():
    # NO costs 0.70 and wins 90% of the time: about +0.19 a contract after a 1.47 cent fee.
    result = bt.evaluate(panel(0.90), n_boot=200)
    assert result["traded"] == 600
    assert result["net_ci95_event"][0] > 0.1
    assert abs(result["mean_fee"] - 0.0147) < 1e-9


def test_fair_prices_lose_the_fee():
    result = bt.evaluate(panel(0.70, seed=3), n_boot=200)
    assert result["mean_net"] < 0
    assert result["net_ci95_event"][0] < 0 < result["net_ci95_event"][1] + 0.05


def test_fee_is_on_the_direct_account_grid():
    # 0.07 x 0.3 x 0.7 = 0.0147 exactly, so a one-lot pays no rounding on the $0.0001 grid.
    one = bt.evaluate(panel(0.7), lot=1, n_boot=10)["mean_fee"]
    hundred = bt.evaluate(panel(0.7), lot=100, n_boot=10)["mean_fee"]
    assert abs(one - 0.0147) < 1e-12 and abs(hundred - one) < 1e-12
    # 0.07 x 0.31 x 0.69 = 0.014973, rounded up to 0.0150 on one lot.
    assert abs(bt.evaluate(panel(0.7, bid=0.31), lot=1, n_boot=10)["mean_fee"] - 0.015) < 1e-12


def test_entry_never_uses_quotes_outside_the_window_or_at_close():
    opened = BASE
    late = candle(opened + timedelta(hours=25), 0.4, 0.5)
    at_close = candle(opened + timedelta(hours=2), 0.4, 0.5)
    no_bid = candle(opened + timedelta(hours=1), 0.0, 0.5)
    assert bt.entry(market(0, 0, "no", 48, [no_bid, late])) is None
    assert bt.entry(market(0, 0, "no", 2, [at_close])) is None
    assert bt.entry(market(0, 0, "no", 48, [late]), window_hours=30) == (0.4, 0.5)


def test_sampling_keeps_events_whole():
    rows = [{"event": f"E{i // 4}", "ticker": f"T{i}"} for i in range(40)]
    chosen = sample_events(rows, 10, "t")
    assert len(chosen) == 8
    assert {r["event_size"] for r in chosen} == {4}


def test_backtest_cli_on_a_panel(tmp_path, capsys):
    path = tmp_path / "raw.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in panel(0.9, n=60)))
    argv = ["backtest", "nothing-ever-happens", "--panel", str(path), "--set", "min_volume=0"]
    assert main(argv) == 0
    out = json.loads((tmp_path / "results_nothing-ever-happens.json").read_text())
    assert out["traded"] == 60 and out["settings"]["lot"] == 1
    assert out["options"] == {"window_hours": 24, "min_volume": 0}
    assert "mean_net" in capsys.readouterr().out


# paper runner at scale --------------------------------------------------------------------


class Exchange:
    def __init__(self):
        self.now = 1000.0
        self.results = {}
        self.book_calls = 0

    def clock(self):
        return self.now

    def book(self, ticker):
        self.book_calls += 1
        return Book(ticker, ((D("0.20"), D(100)),), ((D("0.25"), D(100)),), self.now)

    def lookup(self, tickers):
        return {t: {"status": "active", "result": self.results.get(t, "")} for t in tickers}


def rig(tmp_path, limits=PAPER_LIMITS, cash=D(10_000)):
    ex = Exchange()
    venue = PaperVenue(CachedBooks(ex.book, clock=ex.clock), cash=cash, clock=ex.clock)
    engine = Engine(venue, tmp_path / "j.sqlite", limits, kill_file=tmp_path / "K", clock=ex.clock)
    return ex, venue, engine


def test_paper_limits_enter_every_market_until_cash_runs_out(tmp_path):
    ex, venue, engine = rig(tmp_path, cash=D(100))  # each NO locks 0.80 plus a 0.0112 fee
    summary = run_pass(engine, [{"ticker": f"T{i}"} for i in range(300)], ex.lookup)
    assert summary.entered == 123
    assert summary.stopped == "rejected: insufficient balance"
    assert engine.reconcile() == []


def test_one_book_fetch_per_order(tmp_path):
    ex, _, engine = rig(tmp_path)
    run_pass(engine, [{"ticker": f"T{i}"} for i in range(50)], ex.lookup)
    assert ex.book_calls == 50


def test_listing_without_bid_skips_the_book_fetch(tmp_path):
    ex, _, engine = rig(tmp_path)
    markets = [
        {"ticker": "A", "yes_bid_dollars": "0.0000"},
        {"ticker": "B", "yes_bid_dollars": "0.2"},
    ]
    summary = run_pass(engine, markets, ex.lookup)
    assert (summary.no_bid, summary.entered, ex.book_calls) == (1, 1, 1)


def test_settlement_uses_one_batch_lookup(tmp_path):
    ex, _, engine = rig(tmp_path)
    run_pass(engine, [{"ticker": f"T{i}"} for i in range(5)], ex.lookup)
    calls = []

    def lookup(tickers):
        calls.append(len(tickers))
        return {t: {"result": "no"} for t in tickers}

    assert run_pass(engine, [], lookup).settled == 5
    assert calls == [5]
    assert engine.ledger.realized == D("0.944")  # 5 x (0.20 received - 0.0112 fee)


def test_total_loss_check_can_be_disabled_but_is_on_by_default():
    ledger = Ledger()
    for i in range(300):
        ledger.apply(Fill(f"f{i}", "o", f"T{i}", Side.SELL, D(1), D("0.2"), D(0), True, 0.0))
    book = Book("X", ((D("0.20"), D(10)),), ((D("0.25"), D(10)),), 0.0)
    req = OrderRequest("X", Side.SELL, D(1), D("0.20"), tif=TimeInForce.IOC)
    assert "total worst-case" in check_order(req, RiskLimits(), ledger, {}, book, 0.0, 0)
    assert check_order(req, PAPER_LIMITS, ledger, {}, book, 0.0, 0) is None
    assert RiskLimits().max_total_loss == D(200)


def test_report_marks_unquoted_positions_at_the_worst_case(tmp_path):
    ex, _, engine = rig(tmp_path)
    run_pass(engine, [{"ticker": "A"}, {"ticker": "B"}], ex.lookup)
    listing = [{"ticker": "A", "yes_bid_dollars": "0.10", "yes_ask_dollars": "0.12"}]
    out = report(engine, listing)
    assert out["open_positions"] == 2 and out["held_unquoted"] == 1
    # A: 0.1888 net, marked at ask 0.12 -> +0.0688. B: 0.1888, marked at 1 -> -0.8112.
    assert D(out["mark_nav"]) == D("-0.7424")
    assert listing_book({"ticker": "Z", "yes_bid_dollars": "0"}, 0).best_bid is None


def test_batch_lookup_falls_back_to_the_archive():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path == "/markets":
            asked = request.url.params["tickers"].split(",")
            return httpx.Response(
                200, json={"markets": [{"ticker": t} for t in asked if t not in ("OLD", "GONE")]}
            )
        if request.url.path == "/historical/markets/OLD":
            return httpx.Response(200, json={"market": {"ticker": "OLD", "result": "no"}})
        return httpx.Response(404)

    client = ReadClient("https://x.test", per_second=1000, transport=httpx.MockTransport(handler))
    tickers = [f"T{i}" for i in range(150)] + ["OLD", "GONE"]
    found = KalshiData(client=client).markets(tickers)
    assert len(found) == 151 and found["OLD"]["result"] == "no"
    assert seen.count("/markets") == 2


def test_partial_fill_is_counted_as_partial(tmp_path):
    ex, _, engine = rig(tmp_path)
    ex.book = lambda t: Book(t, ((D("0.20"), D("0.16")),), ((D("0.25"), D(1)),), ex.now)
    engine.venue.book_source = CachedBooks(ex.book, clock=ex.clock)
    summary = run_pass(engine, [{"ticker": "A"}], ex.lookup)
    assert (summary.partial, summary.no_fill, engine.ledger.contracts("A")) == (1, 0, D("-0.16"))


def test_popular_filters_by_24h_volume_and_caps():
    from predmark.strategies.nothing_ever_happens import popular

    ms = [{"ticker": f"T{i}", "volume_24h_fp": str(i * 1000)} for i in range(30)]
    chosen = popular(ms, min_volume=10_000, top=5)
    assert [m["ticker"] for m in chosen] == ["T29", "T28", "T27", "T26", "T25"]
    assert len(popular(ms, min_volume=10_000, top=None)) == 20


def test_volume_rule_uses_only_volume_traded_before_entry():
    opened = BASE

    def c(hours, volume, bid=0.3, archived=False):
        out = candle(opened + timedelta(hours=hours), bid, bid + 0.02, archived)
        out["volume" if archived else "volume_fp"] = f"{volume:.2f}"
        return out

    row = market(0, 0, "no", 48, [c(1, 4000), c(2, 4000, bid=0.4, archived=True), c(3, 4000, 0.5)])
    assert bt.entry(row, min_volume=10_000) == (0.5, 0.52)  # 12,000 traded by hour 3
    assert bt.entry(row, min_volume=0) == (0.3, 0.32)
    assert bt.entry(row, min_volume=20_000) is None


def test_unfunded_markets_are_skipped_not_stopped_at(tmp_path):
    ex, _, engine = rig(tmp_path)
    markets = [{"ticker": f"T{i}", "exchange_index": i % 2} for i in range(6)]
    summary = run_pass(engine, markets, ex.lookup, tradable=lambda m: m["exchange_index"] == 0)
    assert (summary.entered, summary.unfunded, summary.stopped) == (3, 3, None)


def test_funded_reads_shard_balances_only_where_the_venue_has_them(tmp_path):
    from predmark.strategies.base import funded

    ex, venue, engine = rig(tmp_path)
    assert funded(engine)({"exchange_index": 3})
    venue.shard_balances = lambda: {0: D(5), 2: D(0), 3: D("0.01")}
    ok = funded(engine)
    assert [ok({"exchange_index": i}) for i in range(4)] == [True, False, False, True]
    assert ok({"ticker": "no index given"})  # Kalshi's catch-all shard is 0

"""Data collections: the registry and runner, and the Jev forecast collector."""

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal as D
from pathlib import Path

import httpx
import pytest

from predmark import collect, jev
from predmark.collect import jev_forecasts as jf
from predmark.collect import runner
from predmark.collect.__main__ import main
from predmark.collect.base import Collector, Dataset

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 8, 14, 0, tzinfo=UTC)


def test_registry():
    assert "jev-forecasts" in collect.names()
    assert collect.get("jev-forecasts").used_by == (jf.EXPERIMENT,)
    with pytest.raises(KeyError, match="unknown collector"):
        collect.get("nope")


def test_dataset_appends_and_reads(tmp_path):
    ds = Dataset(tmp_path / "x" / "d.jsonl")
    assert list(ds) == [] and ds.keys("k") == set()
    with ds.appender() as out:
        out.write({"k": 1})
    with ds.appender() as out:
        out.write({"k": 2, "v": D("0.5")})
    assert ds.keys("k") == {1, 2}
    assert list(ds)[1]["v"] == "0.5"


@pytest.fixture
def two(monkeypatch):
    def ok(out_dir, log=print, **options):
        (out_dir / "data.jsonl").write_text("{}\n")
        return {"options": options}

    def boom(out_dir, log=print, **options):
        raise RuntimeError("boom")

    fakes = {"ok": Collector("ok", "t", ok), "boom": Collector("boom", "t", boom)}
    monkeypatch.setattr(runner, "names", lambda: list(fakes))
    monkeypatch.setattr(runner, "get", fakes.__getitem__)


def test_runner_gives_each_collector_a_directory_and_logs_runs(tmp_path, two):
    out = runner.run_all(tmp_path, log=lambda m: None)
    assert out["ok"]["ok"] is True and out["boom"]["ok"] is False
    assert (tmp_path / "collect/ok/data.jsonl").exists()
    (ok_run,) = Dataset(tmp_path / "collect/ok/runs.jsonl")
    (boom_run,) = Dataset(tmp_path / "collect/boom/runs.jsonl")
    assert ok_run["ok"] is True and boom_run["error"] == "RuntimeError: boom"


def test_cli_list(capsys):
    assert main(["list"]) == 0
    assert "jev-forecasts" in capsys.readouterr().out


# --- Jev forecasts --------------------------------------------------------------------------


def test_preregistration_hash_is_pinned():
    prereg = ROOT / jf.EXPERIMENT / "preregistration.md"
    assert hashlib.sha256(prereg.read_bytes()).hexdigest() == jf.PREREG_SHA256


def market(ticker, event="E", bid="0.6000", ask="0.6200", **kw):
    return {
        "ticker": ticker,
        "event_ticker": event,
        "title": f"Will {ticker} happen?",
        "yes_sub_title": "Yes",
        "close_time": "2026-11-20T00:00:00Z",
        "rules_primary": "If it happens, resolves Yes.",
        "rules_secondary": "",
        "volume_24h_fp": "50000",
        "yes_bid_dollars": bid,
        "yes_ask_dollars": ask,
        "yes_bid_size_fp": "120",
        "last_price_dollars": "0.6100",
        **kw,
    }


def test_state_text_has_no_prices_or_volume():
    text = jf.state_text(market("A"), NOW)
    assert "2026-11-08T14:00" in text and "Will A happen?" in text
    for leaked in ("0.60", "0.62", "0.61", "50000", "120"):
        assert leaked not in text


@pytest.mark.parametrize(
    "p,bid,ask,bet",
    [
        ("0.90", "0.60", "0.62", "yes"),  # 0.90 - 0.62 - 0.02 = 0.26
        ("0.20", "0.60", "0.62", "no"),  # 0.60 - 0.20 - 0.02 = 0.38
        ("0.61", "0.60", "0.62", None),
        ("0.69", "0.60", "0.62", "yes"),  # exactly the margin
        ("0.68", "0.60", "0.62", None),  # 0.04
        ("0.90", None, None, None),
        ("0.10", "0.60", None, "no"),
    ],
)
def test_decide(p, bid, ask, bet):
    d = jf.decide(D(p), D(bid) if bid else None, D(ask) if ask else None)
    assert d["bet"] == bet
    assert d["price"] == ({"yes": ask, "no": bid}[bet] if bet else None)


def test_empty_sides_are_not_prices():
    assert jf._price("0.0000") is None and jf._price("1.0000") is None
    assert jf._price(None) is None and jf._price("0.42") == D("0.42")


def fake_jev(tmp_path, probs, fail=()):
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        ticker = body["state"].split("Will ")[1].split(" happen")[0]
        if ticker in fail:
            return httpx.Response(400, json={"detail": "max_tokens_exceeded"})
        return httpx.Response(
            200,
            json={
                "model": "jev-1.14.0",
                "answers": {"resolve_yes": {"type": "noul", "noul": probs[ticker]}},
                "usage": {"input_tokens": 300, "output_tokens": 1},
            },
        )

    client = jev.JevClient(
        api_key="k",
        model=jf.MODEL,
        log_path=tmp_path / "cost.jsonl",
        per_second=1000,
        transport=httpx.MockTransport(handler),
    )
    return client, seen


def test_collect_records_forecast_quote_and_decision_once(tmp_path):
    markets = [
        market("A", "E1"),
        market("B", "E2"),
        market("C", "E3"),
        market("N", "E4", bid="0.0000", ask="1.0000"),
        market("X", "E5"),
    ]
    probs = {"A": 0.95, "B": 0.05, "C": 0.61, "N": 0.5}
    client, seen = fake_jev(tmp_path, probs, fail={"X"})
    ds = Dataset(tmp_path / "f.jsonl")

    s = jf.collect(markets, NOW, client, ds, clock=lambda: NOW)
    assert (s.forecast, s.bets, s.errors) == (4, 2, 1)
    rows = {r["ticker"]: r for r in ds}
    assert rows["A"]["decision"]["bet"] == "yes" and rows["A"]["decision"]["price"] == "0.6200"
    assert rows["B"]["decision"]["bet"] == "no" and rows["B"]["decision"]["price"] == "0.6000"
    assert rows["C"]["decision"]["bet"] is None and rows["C"]["p_yes"] == 0.61
    assert rows["N"]["decision"] == {
        "bet": None,
        "price": None,
        "yes_edge": None,
        "no_edge": None,
        "margin": "0.05",
    }
    assert rows["C"]["quote"]["yes_bid_dollars"] == "0.6000"
    assert "error" in rows["X"] and "decision" not in rows["X"]
    assert all(r["prereg_sha256"] == jf.PREREG_SHA256 for r in rows.values())
    assert seen[0]["model"] == "jev-1.14.0"
    assert seen[0]["questions"] == {"resolve_yes": jf.QUESTION}

    s = jf.collect(markets, NOW, client, ds, clock=lambda: NOW)
    assert s.skipped == 5 and s.forecast == 0  # failed forecasts are not retried
    assert len(seen) == 5


def test_collect_stops_at_spend_cap(tmp_path):
    markets = [market(t, t) for t in "ABC"]
    client, _ = fake_jev(tmp_path, dict.fromkeys("ABC", 0.61))
    # Each forecast bills about $0.000013 and is bounded at about $0.000046 beforehand.
    client.cap_usd = D("0.00006")
    s = jf.collect(markets, NOW, client, Dataset(tmp_path / "f.jsonl"), clock=lambda: NOW)
    assert s.forecast == 2 and s.stopped.startswith("spend cap")


class FakeData:
    def __init__(self, markets):
        self.markets = markets

    def open_markets(self, limit_pages=None):
        return self.markets


def test_run_uses_the_popular_universe(tmp_path):
    markets = [market("A", "E1"), market("Q", "E2", volume_24h_fp="10")]
    client, seen = fake_jev(tmp_path, {"A": 0.5, "Q": 0.5})
    out = jf.run(tmp_path, log=lambda m: None, data=FakeData(markets), client=client)
    assert out["universe"] == 1 and out["forecast"] == 1
    assert [r["ticker"] for r in Dataset(tmp_path / "forecasts.jsonl")] == ["A"]


# --- Kalshi listing snapshots ---------------------------------------------------------------


def test_listing_snapshot_keeps_every_popular_market_ranked(tmp_path):
    from predmark.collect import kalshi_listing as kl
    from predmark.strategies.nothing_ever_happens import MIN_VOLUME_24H

    markets = [
        market("LOW", volume_24h_fp="10"),
        market("MID", volume_24h_fp=str(MIN_VOLUME_24H)),
        market("TOP", volume_24h_fp="99999", rules_primary="long text"),
    ]
    clock = lambda: NOW  # noqa: E731
    out = kl.run(tmp_path, log=lambda m: None, data=FakeData(markets), clock=clock)
    assert out["listed"] == 3 and out["recorded"] == 2
    assert out["path"] == "snapshots/2026-11-08.jsonl.gz"
    kl.run(tmp_path, log=lambda m: None, data=FakeData(markets), clock=clock)
    rows = list(kl.read(tmp_path / out["path"]))
    assert [(r["ticker"], r["rank"]) for r in rows] == [("TOP", 1), ("MID", 2)] * 2
    assert set(rows[0]) == {"listed_at", "rank", *kl.FIELDS}
    assert rows[0]["listed_at"] == "2026-11-08T14:00:00+00:00"
    assert rows[0]["yes_bid_dollars"] == "0.6000" and rows[0]["volume_24h_fp"] == "99999"


def test_listing_snapshot_is_registered():
    assert "kalshi-listing" in collect.names()

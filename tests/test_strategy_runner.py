"""The strategy registry, the runner that runs every strategy, and CLI option parsing."""

import json

import pytest

from predmark import strategies
from predmark.cli import main, parse_options
from predmark.strategies import runner
from predmark.strategies.base import Strategy


def test_registry_lists_nothing_ever_happens():
    assert "nothing-ever-happens" in strategies.names()
    s = strategies.get("nothing-ever-happens")
    assert s.backtest is not None
    with pytest.raises(KeyError, match="unknown strategy"):
        strategies.get("nope")


def fake(name, fail=False):
    def run(journal, kill_file, log=print, **options):
        if fail:
            raise RuntimeError("boom")
        journal.write_text("")  # stands in for the engine's journal
        return {"journal": str(journal), "kill": str(kill_file), "options": options}

    return Strategy(name, "test", run)


@pytest.fixture
def two(monkeypatch):
    registry = {"good": fake("good"), "bad": fake("bad", fail=True)}
    monkeypatch.setattr(strategies, "registry", lambda: registry)
    monkeypatch.setattr(runner, "names", lambda: list(registry))
    monkeypatch.setattr(runner, "get", lambda n: registry[n])
    return registry


def test_each_strategy_gets_its_own_journal_and_the_shared_kill_file(tmp_path, two):
    out = runner.run_one("good", tmp_path, x=1)
    assert out == {
        "journal": str(tmp_path / "good.sqlite"),
        "kill": str(tmp_path / "KILL"),
        "options": {"venue": "paper", "x": 1},
    }


def test_demo_gets_its_own_journal(tmp_path, two):
    out = runner.run_one("good", tmp_path, log=lambda m: None, venue="kalshi-demo")
    assert out["journal"] == str(tmp_path / "good.kalshi-demo.sqlite")
    assert out["options"] == {"venue": "kalshi-demo"}
    with pytest.raises(ValueError, match="unknown venue"):
        runner.journal_path(tmp_path, "good", "kalshi-prod")


def test_cli_rejects_a_production_venue(tmp_path):
    with pytest.raises(SystemExit):
        main(["run", "nothing-ever-happens", "--venue", "prod", "--state-dir", str(tmp_path)])


def test_open_engine_demo_uses_default_limits_and_reconciles(tmp_path, monkeypatch):
    from predmark.execution.kalshi import KalshiVenue
    from predmark.execution.risk import RiskLimits
    from predmark.strategies import base

    calls = []

    class FakeKalshi:
        name = "kalshi"

        def fills(self, since_ts):
            return []

        def open_orders(self):
            calls.append("open_orders")
            return []

        def positions(self):
            return {"STRAY-1": 2}

    def from_env(cls, env, subaccount):
        calls.append((env, subaccount))
        return FakeKalshi()

    monkeypatch.setattr(KalshiVenue, "from_env", classmethod(from_env))
    logs = []
    with pytest.raises(ValueError, match="strategy name"):
        base.open_engine(tmp_path / "j.sqlite", tmp_path / "KILL", "kalshi-demo", data=object())
    engine, _ = base.open_engine(
        tmp_path / "j.sqlite",
        tmp_path / "KILL",
        "kalshi-demo",
        data=object(),
        log=logs.append,
        name="nothing-ever-happens",
    )
    assert calls.pop(0) == ("demo", 2)
    assert engine.limits == RiskLimits()
    assert calls == ["open_orders"]
    assert "position mismatch on STRAY-1" in engine.halt_reason
    assert logs and "halted" in logs[0]
    with pytest.raises(ValueError, match="unknown venue"):
        base.open_engine(tmp_path / "k.sqlite", tmp_path / "KILL", "prod")


def test_one_failing_strategy_does_not_stop_the_others(tmp_path, two):
    out = runner.run_all(tmp_path, log=lambda m: None)
    assert out["good"]["ok"] is True
    assert out["bad"] == {"ok": False, "error": "RuntimeError: boom"}


def test_status_is_none_before_the_first_run(tmp_path):
    assert runner.status("nothing-ever-happens", tmp_path) is None


def test_parse_options():
    assert parse_options(["min-volume=0", "top=50", "cash=1000.5", "label=x"]) == {
        "min_volume": 0,
        "top": 50,
        "cash": 1000.5,
        "label": "x",
    }
    with pytest.raises(SystemExit):
        parse_options(["novalue"])


def test_cli_list(capsys):
    assert main(["list"]) == 0
    assert "nothing-ever-happens" in capsys.readouterr().out


def test_cli_status_all_without_runs(tmp_path, capsys):
    assert main(["status", "all", "--state-dir", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out) == {"nothing-ever-happens": None}

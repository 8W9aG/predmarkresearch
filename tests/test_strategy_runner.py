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
        "options": {"x": 1},
    }


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

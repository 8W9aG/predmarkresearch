"""Run registered strategies on paper, each with its own journal in one state directory.

A kill file named KILL in the state directory halts new orders for every strategy. One strategy
failing does not stop the others: its error is recorded and the rest still run.
"""

from __future__ import annotations

import json
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import get, names
from .base import open_offline, report


def journal_path(state_dir: str | Path, name: str) -> Path:
    return Path(state_dir) / f"{name}.sqlite"


def run_one(
    name: str, state_dir: str | Path, log: Callable[[str], None] = print, **options: Any
) -> dict[str, Any]:
    state = Path(state_dir)
    state.mkdir(parents=True, exist_ok=True)
    return get(name).run(journal_path(state, name), state / "KILL", log=log, **options)


def run_all(
    state_dir: str | Path, log: Callable[[str], None] = print, only: list[str] | None = None
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in only or names():
        log(f"== {name}")
        try:
            out[name] = {"ok": True, "result": run_one(name, state_dir, log)}
        except Exception as exc:  # noqa: BLE001 - one strategy's failure must not stop the rest
            log(traceback.format_exc())
            out[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return out


def status(name: str, state_dir: str | Path) -> dict[str, Any] | None:
    """Offline account state and the last run, or None if the strategy has never run."""
    get(name)
    journal = journal_path(state_dir, name)
    if not journal.exists():
        return None
    out = report(open_offline(journal))
    runs = journal.with_suffix(".runs.jsonl")
    if runs.exists():
        lines = runs.read_text().splitlines()
        out["runs"] = len(lines)
        out["last_run"] = json.loads(lines[-1]) if lines else None
    return out

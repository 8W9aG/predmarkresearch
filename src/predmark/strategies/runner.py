"""Run registered strategies, each with its own journal per venue in one state directory.

Paper journals are `<name>.sqlite`. Other venues get `<name>.<venue>.sqlite`, so a demo pass never
reads a paper journal and the website, which reads `<name>.runs.jsonl`, shows paper results only.

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
from .base import VENUES, open_offline, report


def journal_path(state_dir: str | Path, name: str, venue: str = "paper") -> Path:
    """Return the journal path for a strategy on a venue. Raise ValueError for an unknown venue."""
    if venue not in VENUES:
        raise ValueError(f"unknown venue {venue!r}, expected one of {VENUES}")
    suffix = "" if venue == "paper" else f".{venue}"
    return Path(state_dir) / f"{name}{suffix}.sqlite"


def run_one(
    name: str,
    state_dir: str | Path,
    log: Callable[[str], None] = print,
    venue: str = "paper",
    **options: Any,
) -> dict[str, Any]:
    """Run one pass of a strategy on `venue` with its journal and the shared kill file."""
    state = Path(state_dir)
    state.mkdir(parents=True, exist_ok=True)
    journal = journal_path(state, name, venue)
    return get(name).run(journal, state / "KILL", log=log, venue=venue, **options)


def run_all(
    state_dir: str | Path,
    log: Callable[[str], None] = print,
    only: list[str] | None = None,
    venue: str = "paper",
) -> dict[str, Any]:
    """Run each strategy in `only`, or all of them, and return each one's result by name.

    A failing strategy is logged and recorded as not ok. The others still run.
    """
    out: dict[str, Any] = {}
    for name in only or names():
        log(f"== {name}")
        try:
            out[name] = {"ok": True, "result": run_one(name, state_dir, log, venue)}
        except Exception as exc:  # noqa: BLE001 - one strategy's failure must not stop the rest
            log(traceback.format_exc())
            out[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return out


def status(name: str, state_dir: str | Path, venue: str = "paper") -> dict[str, Any] | None:
    """Offline account state and the last run, or None if the strategy has never run there."""
    get(name)
    journal = journal_path(state_dir, name, venue)
    if not journal.exists():
        return None
    out = report(open_offline(journal))
    runs = journal.with_suffix(".runs.jsonl")
    if runs.exists():
        lines = runs.read_text().splitlines()
        out["runs"] = len(lines)
        out["last_run"] = json.loads(lines[-1]) if lines else None
    return out

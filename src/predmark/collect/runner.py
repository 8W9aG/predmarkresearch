"""Run registered collectors, each in `<state>/collect/<name>/`, and log every run."""

from __future__ import annotations

import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import get, names
from .base import Dataset


def out_dir(state_dir: str | Path, name: str) -> Path:
    return Path(state_dir) / "collect" / name


def run_one(
    name: str, state_dir: str | Path, log: Callable[[str], None] = print, **options: Any
) -> dict[str, Any]:
    """Run one collector and append its summary, or its error, to its `runs.jsonl`."""
    collector = get(name)
    directory = out_dir(state_dir, name)
    directory.mkdir(parents=True, exist_ok=True)
    started = time.time()
    record: dict[str, Any] = {"ts": started, "collector": name, "options": options}
    try:
        record.update(ok=True, result=collector.run(directory, log=log, **options))
    except Exception as exc:
        record.update(ok=False, error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        record["seconds"] = round(time.time() - started)
        with Dataset(directory / "runs.jsonl").appender() as out:
            out.write(record)
    return record


def run_all(state_dir: str | Path, log: Callable[[str], None] = print) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in names():
        log(f"== collect {name}")
        try:
            out[name] = run_one(name, state_dir, log)
        except Exception as exc:  # noqa: BLE001 - one collector's failure must not stop the rest
            log(traceback.format_exc())
            out[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return out

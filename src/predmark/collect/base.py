"""What every collector provides, and the append-only JSONL file they write to."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Collector:
    name: str
    description: str
    # run(out_dir, log, **options) -> JSON-able summary. Writes only under out_dir.
    run: Callable[..., dict[str, Any]]
    # Experiment directories that will analyse this data, shown alongside it.
    used_by: tuple[str, ...] = ()


class Dataset:
    """An append-only JSONL file of records. Each record is written and flushed on its own, so an
    interrupted run keeps everything it recorded."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def __iter__(self) -> Iterator[dict[str, Any]]:
        if not self.path.exists():
            return
        with self.path.open() as fh:
            for line in fh:
                if line.strip():
                    yield json.loads(line)

    def keys(self, field: str) -> set[Any]:
        return {r[field] for r in self if field in r}

    def appender(self) -> _Appender:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return _Appender(self.path.open("a"))


class _Appender:
    def __init__(self, fh):
        self.fh = fh

    def __enter__(self) -> _Appender:
        return self

    def __exit__(self, *exc: object) -> None:
        self.fh.close()

    def write(self, record: dict[str, Any]) -> None:
        self.fh.write(json.dumps(record, sort_keys=True, default=str) + "\n")
        self.fh.flush()

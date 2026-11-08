"""Run the configured literature searches. One failing source never stops the others."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

from . import sources
from .library import Library

DEFAULT_CONFIG = Path("research/literature/sources.yaml")


def harvest(
    config_path: str | Path = DEFAULT_CONFIG,
    library: Library | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    cfg = yaml.safe_load(Path(config_path).read_text())
    library = library or Library()
    since = (date.today() - timedelta(days=int(cfg.get("lookback_days", 1095)))).isoformat()
    limit = int(cfg.get("per_query", 50))
    jobs: list[tuple[str, Callable[[], list[sources.Record]]]] = []
    for q in cfg.get("arxiv", []):
        jobs.append((f"arxiv: {q}", lambda q=q: sources.fetch_arxiv(q, limit)))
    for q in cfg.get("openalex", []):
        jobs.append((f"openalex: {q}", lambda q=q: sources.fetch_openalex(q, limit, since)))
    for q in cfg.get("semantic_scholar", []):
        jobs.append(
            (f"s2: {q}", lambda q=q: sources.fetch_semantic_scholar(q, limit, int(since[:4])))
        )
    for feed in cfg.get("feeds", []):
        jobs.append(
            (f"feed: {feed['name']}", lambda f=feed: sources.fetch_feed(f["url"], f["name"]))
        )

    summary: dict[str, Any] = {"new": 0, "seen": 0, "failed": []}
    for name, job in jobs:
        try:
            records = job()
        except Exception as exc:  # noqa: BLE001 - reported in the summary, then we continue
            summary["failed"].append({"job": name, "error": f"{type(exc).__name__}: {exc}"[:200]})
            log(f"FAILED {name}: {exc}")
            continue
        added = sum(library.add(r) for r in records)
        summary["new"] += added
        summary["seen"] += len(records)
        log(f"{name}: {len(records)} fetched, {added} new")
        library.save()
    library.save()
    library.write_index()
    return summary

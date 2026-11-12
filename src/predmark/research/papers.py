"""Mechanical integrity checks for a paper. They complement review and do not replace it."""

from __future__ import annotations

import json
import re
from pathlib import Path

CITE = re.compile(r"\[@([A-Za-z0-9_:\-]+)\]")
EXPERIMENT = re.compile(r"\b(E\d{3})\b")
MIN_STATUS = {"abstract_read", "full_text_read"}
# Maintained by `scripts/lit.py`. Only each record's key and status are read here.
LIBRARY_PATH = Path("research/literature/library.jsonl")


def read_statuses(path: str | Path = LIBRARY_PATH) -> dict[str, str | None]:
    """Map each library key to its review status."""
    path = Path(path)
    if not path.exists():
        return {}
    records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return {r["key"]: r.get("status") for r in records}


def check_paper(
    path: str | Path,
    library_path: str | Path = LIBRARY_PATH,
    experiments_dir: str | Path = "research/experiments",
) -> list[str]:
    """Return the problems found in a paper's citations and experiment references.

    Each citation must be in the library with a status of `abstract_read` or `full_text_read`.
    Each experiment named must have a directory with a `results.json`. A `results.json` that is
    not valid JSON raises an error.
    """
    text = Path(path).read_text()
    statuses = read_statuses(library_path)
    problems = []
    for key in sorted(set(CITE.findall(text))):
        if key not in statuses:
            problems.append(f"citation {key} is not in the library")
        elif statuses[key] not in MIN_STATUS:
            problems.append(f"citation {key} has status {statuses[key]}: not read yet")
    root = Path(experiments_dir)
    for exp in sorted(set(EXPERIMENT.findall(text))):
        matches = list(root.glob(f"{exp}-*"))
        if not matches:
            problems.append(f"experiment {exp} has no directory")
            continue
        results = matches[0] / "results.json"
        if not results.exists():
            problems.append(f"experiment {exp} has no results.json")
        else:
            json.loads(results.read_text())
    return problems

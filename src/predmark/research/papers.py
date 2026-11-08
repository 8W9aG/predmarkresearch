"""Mechanical integrity checks for a paper. They complement review and do not replace it."""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..lit.library import DEFAULT_PATH, Library

CITE = re.compile(r"\[@([A-Za-z0-9_:\-]+)\]")
EXPERIMENT = re.compile(r"\b(E\d{3})\b")
MIN_STATUS = {"abstract_read", "full_text_read"}


def check_paper(
    path: str | Path,
    library_path: str | Path = DEFAULT_PATH,
    experiments_dir: str | Path = "research/experiments",
) -> list[str]:
    text = Path(path).read_text()
    library = Library(library_path)
    problems = []
    for key in sorted(set(CITE.findall(text))):
        rec = library.records.get(key)
        if rec is None:
            problems.append(f"citation {key} is not in the library")
        elif rec.get("status") not in MIN_STATUS:
            problems.append(f"citation {key} has status {rec.get('status')}: not read yet")
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

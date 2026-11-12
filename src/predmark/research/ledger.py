"""An append-only record of every evaluation, including the disappointing ones.

Backtest statistics are only meaningful alongside the number of things tried, so nothing is
removed. Each experiment gets one holdout look: a second is refused.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path("research/trials.jsonl")


class HoldoutAlreadyUsed(RuntimeError):
    """Error for a second holdout evaluation of one experiment."""


def file_sha256(path: str | Path) -> str:
    """Return the SHA-256 hex digest of a file's bytes."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_trials(path: str | Path = DEFAULT_PATH) -> list[dict[str, Any]]:
    """Return every ledger entry in file order, or an empty list if there is no ledger."""
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def holdout_used(experiment: str, path: str | Path = DEFAULT_PATH) -> bool:
    """Return True if the ledger has a holdout entry for `experiment`."""
    return any(t["experiment"] == experiment and t["split"] == "holdout" for t in read_trials(path))


def claim_holdout(experiment: str, path: str | Path = DEFAULT_PATH) -> None:
    """Raise HoldoutAlreadyUsed if `experiment` has already used its holdout.

    This only checks the ledger. A `record_trial` call with the `holdout` split records the use.
    """
    if holdout_used(experiment, path):
        raise HoldoutAlreadyUsed(
            f"{experiment} has already looked at its holdout. Register a new experiment, and "
            "evaluate it on data collected after this one's holdout."
        )


def record_trial(
    experiment: str,
    split: str,
    prereg: str | Path,
    dataset_sha256: str,
    summary: dict[str, Any],
    path: str | Path = DEFAULT_PATH,
) -> dict[str, Any]:
    """Append one evaluation to the ledger and return the entry.

    `split` must be `dev` or `holdout`. The entry holds the preregistration's hash, so a later
    edit to that file is detectable.
    """
    if split not in {"dev", "holdout"}:
        raise ValueError(f"unknown split {split}")
    entry = {
        "ts": time.time(),
        "experiment": experiment,
        "split": split,
        "prereg_sha256": file_sha256(prereg),
        "dataset_sha256": dataset_sha256,
        "summary": summary,
    }
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
    return entry

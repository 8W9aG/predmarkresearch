"""Checks of the research rules in CLAUDE.md that git history can prove.

CI runs these through `python -m predmark.research check-integrity`. They need the full history,
so a shallow clone fails them.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .ledger import DEFAULT_PATH as LEDGER_PATH
from .ledger import file_sha256, read_trials
from .papers import LIBRARY_PATH, check_paper

EXPERIMENTS_DIR = Path("research/experiments")
PAPERS_DIR = Path("research/papers")

# Experiments run before this repo's history began, with the reason each is exempt from the
# commit-order check. Never add to this list: a new experiment must pass.
PREDATES_HISTORY = {
    "E001": "Run on 2026-11-06 and first committed in b4c3507 together with its results. Its "
    "ledger entries record the hash of the preregistration in the repo, which this check also "
    "verifies.",
}


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=check
    )


def _show(repo: Path, rev: str, path: Path) -> str | None:
    out = _git(repo, "show", f"{rev}:{path.as_posix()}", check=False)
    return out.stdout if out.returncode == 0 else None


def _lines(text: str | None) -> list[str]:
    return [line for line in (text or "").splitlines() if line.strip()]


def check_shallow(repo: Path) -> list[str]:
    out = _git(repo, "rev-parse", "--is-shallow-repository").stdout.strip()
    if out == "true":
        return ["the clone is shallow: fetch the full history (fetch-depth: 0)"]
    return []


def check_ledger_append_only(repo: Path, ledger: Path = LEDGER_PATH) -> list[str]:
    """Each commit's ledger must start with every line its parents had, unchanged and in order."""
    problems = []
    commits = _git(repo, "log", "--format=%H %P", "--full-history", "--", ledger.as_posix())
    for row in commits.stdout.splitlines():
        sha, *parents = row.split()
        new = _lines(_show(repo, sha, ledger))
        for parent in parents:
            old = _lines(_show(repo, parent, ledger))
            if new[: len(old)] != old:
                problems.append(
                    f"commit {sha[:7]} removes or changes existing lines of {ledger} "
                    f"(compared with parent {parent[:7]})"
                )
    return problems


def _first_added(repo: Path, path: Path) -> str | None:
    out = _git(repo, "log", "--diff-filter=A", "--format=%H", "--", path.as_posix())
    shas = out.stdout.split()
    return shas[-1] if shas else None


def _is_strict_ancestor(repo: Path, older: str, newer: str) -> bool:
    if older == newer:
        return False
    return _git(repo, "merge-base", "--is-ancestor", older, newer, check=False).returncode == 0


def check_prereg_order(
    repo: Path, experiments_dir: Path = EXPERIMENTS_DIR, ledger: Path = LEDGER_PATH
) -> list[str]:
    """A preregistration is committed before its results, and is unchanged since evaluation."""
    problems = []
    hashes: dict[str, set[str]] = {}
    for trial in read_trials(repo / ledger):
        hashes.setdefault(trial["experiment"], set()).add(trial["prereg_sha256"])
    for exp_dir in sorted((repo / experiments_dir).glob("E[0-9][0-9][0-9]-*")):
        exp = exp_dir.name[:4]
        prereg = exp_dir / "preregistration.md"
        results = exp_dir / "results.json"
        if not prereg.exists():
            problems.append(f"{exp} has no preregistration.md")
            continue
        for recorded in sorted(hashes.get(exp, set())):
            if recorded != file_sha256(prereg):
                problems.append(
                    f"{exp}'s preregistration.md differs from the version its ledger entry "
                    "evaluated: record changes in amendments.md instead"
                )
        if not results.exists() or exp in PREDATES_HISTORY:
            continue
        rel = exp_dir.relative_to(repo)
        prereg_sha = _first_added(repo, rel / "preregistration.md")
        results_sha = _first_added(repo, rel / "results.json")
        if prereg_sha is None:
            problems.append(f"{exp}'s preregistration.md is not committed")
        elif results_sha is None:
            problems.append(f"{exp}'s results.json is not committed")
        elif not _is_strict_ancestor(repo, prereg_sha, results_sha):
            problems.append(
                f"{exp}'s results.json (commit {results_sha[:7]}) is not committed after its "
                f"preregistration.md (commit {prereg_sha[:7]})"
            )
    return problems


def check_papers(
    repo: Path,
    papers_dir: Path = PAPERS_DIR,
    library: Path = LIBRARY_PATH,
    experiments_dir: Path = EXPERIMENTS_DIR,
) -> list[str]:
    problems = []
    for paper in sorted((repo / papers_dir).glob("*/paper.md")):
        for problem in check_paper(paper, repo / library, repo / experiments_dir):
            problems.append(f"{paper.relative_to(repo)}: {problem}")
    return problems


def check_integrity(repo: str | Path = ".") -> list[str]:
    root = Path(_git(Path(repo), "rev-parse", "--show-toplevel").stdout.strip())
    problems = check_shallow(root)
    if problems:
        return problems
    return check_ledger_append_only(root) + check_prereg_order(root) + check_papers(root)

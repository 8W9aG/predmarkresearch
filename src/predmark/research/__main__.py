"""Research integrity tools for agents: `uv run python -m predmark.research <command>`."""

from __future__ import annotations

import argparse
import json

from .integrity import check_integrity
from .ledger import read_trials
from .papers import check_paper


def main(argv: list[str] | None = None) -> int:
    """Run the research command line and return the exit code.

    The exit code is 1 if a check finds any problem.
    """
    parser = argparse.ArgumentParser(prog="python -m predmark.research")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("trials", help="every evaluation ever run")
    sub.add_parser("check-paper").add_argument("path")
    sub.add_parser(
        "check-integrity",
        help="ledger only grows, preregistrations precede results, every paper passes",
    )
    args = parser.parse_args(argv)
    if args.cmd == "trials":
        for t in read_trials():
            print(json.dumps(t, sort_keys=True))
        return 0
    problems = check_integrity() if args.cmd == "check-integrity" else check_paper(args.path)
    for problem in problems:
        print(f"PROBLEM: {problem}")
    print("ok" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())

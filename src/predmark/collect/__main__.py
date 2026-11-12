"""`python -m predmark.collect list | run <name|all> [--state-dir DIR] [--set key=value ...]`."""

from __future__ import annotations

import argparse
import json

from ..cli import parse_options
from . import registry
from .runner import run_all, run_one


def main(argv: list[str] | None = None) -> int:
    """Run the collect command line and return the exit code."""
    parser = argparse.ArgumentParser(prog="python -m predmark.collect")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    run = sub.add_parser("run")
    run.add_argument("name", help="a collector name, or 'all'")
    run.add_argument("--state-dir", default="data/paper")
    run.add_argument("--set", dest="options", action="append", default=[], metavar="KEY=VALUE")
    args = parser.parse_args(argv)

    if args.cmd == "list":
        for c in registry().values():
            print(f"{c.name}: {c.description}")
        return 0
    if args.name == "all":
        result = run_all(args.state_dir)
    else:
        result = run_one(args.name, args.state_dir, **parse_options(args.options))
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Command line: list, backtest, run, and inspect registered strategies.

Literature and research tools are not here. Agents run them with `python -m predmark.lit` and
`python -m predmark.research`.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from . import strategies
from .strategies.base import VENUES

STATE_DIR = "data/paper"
VENUE_HELP = (
    "paper fills against live production books; kalshi-demo sends orders to Kalshi's demo "
    "exchange and needs KALSHI_KEY_ID and KALSHI_PRIVATE_KEY_PATH for a demo account"
)


def parse_options(pairs: list[str]) -> dict[str, Any]:
    """`--set key=value` pairs. Values are parsed as JSON when they can be, otherwise strings."""
    out: dict[str, Any] = {}
    for pair in pairs:
        key, sep, raw = pair.partition("=")
        if not sep or not key:
            raise SystemExit(f"--set expects key=value, got {pair!r}")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = raw
        out[key.replace("-", "_")] = value
    return out


def _list(args: argparse.Namespace) -> int:
    for s in strategies.registry().values():
        print(f"{s.name}\t{s.description}{'' if s.backtest else '  (no backtest)'}")
    return 0


def _backtest(args: argparse.Namespace) -> int:
    from .backtest.panel import build

    strategy = strategies.get(args.strategy)
    if strategy.backtest is None:
        raise SystemExit(f"{strategy.name} has no backtest")
    panel = args.panel
    if panel is None:
        if not (args.start and args.end):
            raise SystemExit("give --panel, or --start and --end to build one")
        out = args.out or f"data/backtest/{args.start}_{args.end}_{args.series}_{args.max_markets}"
        panel = build(out, args.start, args.end, args.series, args.max_markets, args.window_hours)
    options = {"window_hours": args.window_hours, **parse_options(args.set)}
    result = {"strategy": strategy.name, "options": options, **strategy.backtest(panel, **options)}
    path = Path(args.results or Path(panel).with_name(f"results_{strategy.name}.json"))
    path.write_text(json.dumps(result, indent=2, default=float) + "\n")
    headline = {
        k: v for k, v in result.items() if not isinstance(v, dict) or k == "control_buy_yes"
    }
    print(json.dumps(headline, indent=2, default=float))
    print(f"full results: {path}")
    return 0


def _run(args: argparse.Namespace) -> int:
    from .strategies.runner import run_all, run_one

    if args.strategy == "all":
        if args.set:
            raise SystemExit("--set applies to one strategy, not all")
        result = run_all(args.state_dir, venue=args.venue)
    else:
        options = parse_options(args.set)
        result = run_one(args.strategy, args.state_dir, venue=args.venue, **options)
    print(json.dumps(result, indent=2, default=str))
    return 0


def _status(args: argparse.Namespace) -> int:
    from .strategies.runner import status

    chosen = strategies.names() if args.strategy == "all" else [args.strategy]
    out = {name: status(name, args.state_dir, args.venue) for name in chosen}
    print(json.dumps(out, indent=2, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    names = strategies.names()
    parser = argparse.ArgumentParser(prog="predmark")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="registered strategies")

    bt = sub.add_parser("backtest", help="evaluate a strategy on settled Kalshi markets")
    bt.add_argument("strategy", choices=names)
    bt.add_argument("--panel", help="an existing raw.jsonl, e.g. data/e002/raw.jsonl")
    bt.add_argument("--start", help="first close date, YYYY-MM-DD, when building a panel")
    bt.add_argument("--end", help="close dates before this, YYYY-MM-DD")
    bt.add_argument("--series", type=int, default=300, help="series to sample")
    bt.add_argument("--max-markets", type=int, default=3000, help="candle request budget")
    bt.add_argument("--window-hours", type=int, default=24, help="candle window after open")
    bt.add_argument("--out", help="panel directory")
    bt.add_argument("--results", help="where to write the results JSON")
    bt.add_argument("--set", action="append", default=[], help="strategy option key=value")

    run = sub.add_parser("run", help="one pass, on paper or the Kalshi demo exchange")
    run.add_argument("strategy", choices=[*names, "all"])
    run.add_argument("--venue", choices=VENUES, default="paper", help=VENUE_HELP)
    run.add_argument("--state-dir", default=STATE_DIR, help="one journal per strategy")
    run.add_argument("--set", action="append", default=[], help="strategy option key=value")

    st = sub.add_parser("status", help="account state from the journal, offline")
    st.add_argument("strategy", choices=[*names, "all"])
    st.add_argument("--venue", choices=VENUES, default="paper", help="which journal to read")
    st.add_argument("--state-dir", default=STATE_DIR)

    args = parser.parse_args(argv)
    handlers = {"list": _list, "backtest": _backtest, "run": _run, "status": _status}
    return handlers[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())

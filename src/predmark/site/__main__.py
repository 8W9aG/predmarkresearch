"""Build the static website: `uv run python -m predmark.site [--from-url URL | --from-modal]`."""

from __future__ import annotations

import argparse
import os

from .build import VOLUME, build, fetch_from_modal, fetch_from_url

FETCHED = "data/site-state"


def main(argv: list[str] | None = None) -> int:
    """Build the website, after an optional fetch of run logs, and return 0."""
    parser = argparse.ArgumentParser(prog="python -m predmark.site")
    parser.add_argument("--out", default="site")
    parser.add_argument("--state-dir", default="data/paper", help="where *.runs.jsonl are read")
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--from-url",
        nargs="?",
        const=os.environ.get("PREDMARK_RUN_LOGS_URL", ""),
        metavar="URL",
        help=(
            f"first copy run logs from the Modal run_logs endpoint into {FETCHED}. "
            "Needs no credential. Defaults to $PREDMARK_RUN_LOGS_URL"
        ),
    )
    source.add_argument(
        "--from-modal",
        action="store_true",
        help=f"first copy run logs from the Modal volume {VOLUME} (needs a Modal token)",
    )
    args = parser.parse_args(argv)
    state = args.state_dir
    if args.from_url is not None:
        if not args.from_url:
            parser.error("--from-url needs a URL or PREDMARK_RUN_LOGS_URL")
        state = FETCHED
        copied = fetch_from_url(state, args.from_url)
        print(f"copied {len(copied)} run logs from {args.from_url}: {', '.join(copied) or 'none'}")
    elif args.from_modal:
        state = FETCHED
        copied = fetch_from_modal(state)
        print(f"copied {len(copied)} run logs from Modal: {', '.join(copied) or 'none'}")
    out = build(args.out, state)
    print(f"wrote {out}/index.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

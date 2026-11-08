"""Build the static website: `uv run python -m predmark.site [--from-modal]`."""

from __future__ import annotations

import argparse

from .build import VOLUME, build, fetch_from_modal


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m predmark.site")
    parser.add_argument("--out", default="site")
    parser.add_argument("--state-dir", default="data/paper", help="where *.runs.jsonl are read")
    parser.add_argument(
        "--from-modal",
        action="store_true",
        help=f"first copy run logs from the Modal volume {VOLUME} into data/site-state",
    )
    args = parser.parse_args(argv)
    state = args.state_dir
    if args.from_modal:
        state = "data/site-state"
        copied = fetch_from_modal(state)
        print(f"copied {len(copied)} run logs from Modal: {', '.join(copied) or 'none'}")
    out = build(args.out, state)
    print(f"wrote {out}/index.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

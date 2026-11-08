"""Literature index tools for the /lit-review agent: `uv run python -m predmark.lit <command>`."""

from __future__ import annotations

import argparse
import json
import sys

from .harvest import harvest
from .library import Library


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m predmark.lit")
    sub = parser.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("harvest", help="fetch sources and rewrite INDEX.md")
    h.add_argument("--config", default="research/literature/sources.yaml")
    ls = sub.add_parser("list")
    ls.add_argument("--status")
    ls.add_argument("--limit", type=int, default=50)
    sub.add_parser("index", help="regenerate research/literature/INDEX.md")
    sub.add_parser("next", help="show the next unreviewed record")
    sub.add_parser("show").add_argument("key")
    rv = sub.add_parser("review")
    rv.add_argument("key")
    rv.add_argument("--status")
    rv.add_argument("--relevance", type=int)
    rv.add_argument("--note")
    rv.add_argument("--tags")
    args = parser.parse_args(argv)

    if args.cmd == "harvest":
        print(json.dumps(harvest(args.config), indent=2))
        return 0
    lib = Library()
    if args.cmd == "list":
        rows = [r for r in lib.records.values() if not args.status or r["status"] == args.status]
        rows.sort(key=lambda r: (r.get("citations") or 0, r.get("date") or ""), reverse=True)
        for r in rows[: args.limit]:
            print(f"{r['key']}\t{r['status']}\t{r.get('date')}\t{r.get('citations')}\t{r['title']}")
        print(f"{len(rows)} records", file=sys.stderr)
    elif args.cmd == "index":
        lib.write_index()
        print(f"indexed {len(lib.records)} records")
    elif args.cmd == "next":
        rec = lib.next_new()
        print(json.dumps(rec, indent=2) if rec else "nothing left to review")
    elif args.cmd == "show":
        print(json.dumps(lib.records[args.key], indent=2))
    elif args.cmd == "review":
        fields = {
            "status": args.status,
            "relevance": args.relevance,
            "note": args.note,
            "tags": args.tags.split(",") if args.tags else None,
        }
        print(
            json.dumps(
                lib.review(args.key, **{k: v for k, v in fields.items() if v is not None}), indent=2
            )
        )
        lib.save()
        lib.write_index()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

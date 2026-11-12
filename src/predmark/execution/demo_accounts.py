"""Manage cash on the Kalshi demo account: subaccounts and exchange indexes (shards).

    uv run python -m predmark.execution.demo_accounts balances
    uv run python -m predmark.execution.demo_accounts create
    uv run python -m predmark.execution.demo_accounts transfer --to-sub 2 --to-shard 2 --dollars 10

Demo only: the venue is always built with env="demo". Moves stay inside the account.

Each journal trades in its own subaccount (`DEMO_SUBACCOUNTS`), and Kalshi funds each exchange
index separately, so a new strategy needs a subaccount with cash on each index it trades. A
transfer is sent once and never retried. Read `balances` to see whether it landed, because Kalshi
processes transfers asynchronously.
"""

from __future__ import annotations

import argparse
import json
import uuid
from decimal import Decimal
from typing import Any

from .kalshi import MAX_SUBACCOUNT, KalshiVenue

CENTICENTS_PER_DOLLAR = 10_000  # transfer amounts are in units of $0.0001


def _call(venue: KalshiVenue, method: str, path: str, **kw: Any) -> Any:
    resp = venue.http.request(method, path, headers=venue._headers(method, path), **kw)
    if resp.status_code >= 400:
        raise SystemExit(f"{method} {path}: HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json() if resp.content else {}


def balances(venue: KalshiVenue) -> list[dict[str, Any]]:
    """Return cash per subaccount and exchange index, sorted by both."""
    rows = _call(venue, "GET", "/portfolio/subaccounts/balances")["subaccount_balances"]
    return sorted(rows, key=lambda r: (r["subaccount_number"], r["exchange_index"]))


def create(venue: KalshiVenue) -> int:
    """Create the next numbered demo subaccount and return its number."""
    return int(_call(venue, "POST", "/portfolio/subaccounts", json={})["subaccount_number"])


def transfer_body(
    from_sub: int, from_shard: int, to_sub: int, to_shard: int, dollars: Decimal
) -> dict[str, Any]:
    """Build the body of a cash transfer between subaccounts and exchange indexes.

    Raises ValueError if `dollars` is not a positive multiple of $0.0001, a subaccount is out
    of range, or the source and destination are the same.
    """
    amount = dollars * CENTICENTS_PER_DOLLAR
    if amount <= 0 or amount != amount.to_integral_value():
        raise ValueError(f"{dollars} is not a positive multiple of $0.0001")
    for sub in (from_sub, to_sub):
        if not 0 <= sub <= MAX_SUBACCOUNT:
            raise ValueError(f"subaccount {sub} outside 0..{MAX_SUBACCOUNT}")
    if (from_sub, from_shard) == (to_sub, to_shard):
        raise ValueError("source and destination are the same")
    body: dict[str, Any] = {
        "source": "event_contract",
        "destination": "event_contract",
        "amount": int(amount),
        "source_exchange_shard": from_shard,
        "destination_exchange_shard": to_shard,
        "source_subaccount": from_sub,
        "destination_subaccount": to_sub,
    }
    # Kalshi refuses an idempotency key on any transfer that involves a subaccount (checked on
    # demo, 2026-11-11). Without one a resend could move the money twice, so nothing resends.
    if from_sub == to_sub == 0:
        body["client_transfer_id"] = str(uuid.uuid4())
    return body


def main(argv: list[str] | None = None) -> int:
    """Run the demo account command line and return the exit code.

    A transfer is sent to the demo venue once and never retried.
    """
    parser = argparse.ArgumentParser(prog="python -m predmark.execution.demo_accounts")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("balances", help="cash per subaccount and exchange index")
    sub.add_parser("create", help="create the next numbered subaccount")
    t = sub.add_parser("transfer", help="move cash between subaccounts and exchange indexes")
    t.add_argument("--from-sub", type=int, default=0)
    t.add_argument("--from-shard", type=int, default=0)
    t.add_argument("--to-sub", type=int, required=True)
    t.add_argument("--to-shard", type=int, default=0)
    t.add_argument("--dollars", type=Decimal, required=True)
    args = parser.parse_args(argv)

    try:
        venue = KalshiVenue.from_env(env="demo")
    except KeyError as exc:
        raise SystemExit(f"set {exc.args[0]} for a Kalshi demo account (see .env.example)") from exc
    if args.cmd == "balances":
        out: Any = balances(venue)
    elif args.cmd == "create":
        out = {"subaccount_number": create(venue)}
    else:
        body = transfer_body(
            args.from_sub, args.from_shard, args.to_sub, args.to_shard, args.dollars
        )
        out = {
            "request": body,
            "response": _call(
                venue, "POST", "/portfolio/intra_exchange_instance_transfer", json=body
            ),
        }
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

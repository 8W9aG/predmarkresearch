"""Jev forecasts of popular Kalshi markets, for E003.

Ask Jev whether each popular Kalshi market resolves YES, and record the answer with the quote at
that time and the bet it implies. Never trades: the decision is recorded, not sent anywhere.

Forward only. We do not know Jev's training cutoff, so forecasts of markets that have already
resolved could score memorised outcomes. Every forecast here is made before its market resolves.

Jev sees the decision time and the market's title, YES outcome, scheduled close and rules. It does
not see prices or volumes, so its probability is independent of the market's. The listing quote is
recorded next to it but never sent. Each market is forecast once, on the first run that finds it in
the universe. The universe is the one Nothing Ever Happens uses: 24-hour volume of at least
MIN_VOLUME_24H on the live listing, busiest first, at most TOP markets.

Rules text is untrusted third-party data. Jev only scores it, but planted text could bias it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from .. import jev
from ..execution.fees import kalshi_fee
from ..strategies.nothing_ever_happens import MIN_VOLUME_24H, TOP, popular
from .base import Collector, Dataset

NAME = "jev-forecasts"
EXPERIMENT = "research/experiments/E003-jev-forecast"
# SHA-256 of EXPERIMENT/preregistration.md, written with every forecast. Modal gets only the
# package, not the file, so the hash is pinned here and a test checks the file still matches it.
PREREG_SHA256 = "4117447b45b46a42dc14c05144f02799937f2794d0a38fd7a36fdef4577e8ebf"
# E003 fixes the model and the question. Changing either is a new dataset, not an edit.
MODEL = "jev-1.14.0"
QUESTION_VERSION = "resolve-yes-v1"
QUESTION = jev.noul(
    "Will this market resolve YES?",
    true="The market resolves YES under its rules.",
    false="The market resolves NO under its rules.",
)
# A bet needs an edge net of the taker fee of at least this. Both edges are recorded, so other
# margins can be studied, but E003's decision uses this one.
MARGIN = Decimal("0.05")
CAP_USD = Decimal("0.25")
# Listing fields recorded with each forecast, never sent to Jev.
QUOTE_FIELDS = (
    "yes_bid_dollars",
    "yes_ask_dollars",
    "yes_bid_size_fp",
    "yes_ask_size_fp",
    "last_price_dollars",
    "volume_24h_fp",
    "open_interest_fp",
)


@dataclass
class Summary:
    """Counts from one collection run, the spend in USD, and why it stopped early, if it did."""

    universe: int = 0
    forecast: int = 0
    skipped: int = 0  # forecast on an earlier run
    bets: int = 0  # decisions to bet, recorded only
    errors: int = 0
    spent_usd: str = "0"
    stopped: str | None = None


def state_text(market: dict[str, Any], decided_at: datetime) -> str:
    """What Jev sees: no prices, volumes or anything from after the decision."""
    lines = [
        f"Decision time: {decided_at.isoformat(timespec='minutes')}",
        f"Market: {market.get('title') or ''}",
    ]
    if market.get("yes_sub_title"):
        lines.append(f"YES outcome: {market['yes_sub_title']}")
    if market.get("close_time"):
        lines.append(f"Scheduled close: {market['close_time']}")
    rules = "\n".join(r for r in (market.get("rules_primary"), market.get("rules_secondary")) if r)
    lines.append(f"Rules:\n{rules}")
    return "\n".join(lines)


def _price(value: Any) -> Decimal | None:
    """A listing quote, or None when that side is empty (Kalshi lists 0 or 1 then)."""
    if value is None:
        return None
    price = Decimal(str(value))
    return price if 0 < price < 1 else None


def decide(p: Decimal, bid: Decimal | None, ask: Decimal | None) -> dict[str, Any]:
    """The bet one contract would take: YES at the ask, NO at the bid (selling YES), or none.

    YES edge = p - ask - fee(ask), NO edge = bid - p - fee(bid), with the Kalshi taker fee on one
    contract at multiplier 1. Bet the larger edge if it is at least MARGIN.
    """
    yes = p - ask - kalshi_fee(Decimal(1), ask) if ask is not None else None
    no = bid - p - kalshi_fee(Decimal(1), bid) if bid is not None else None
    best = max((e for e in (yes, no) if e is not None), default=None)
    bet = None
    if best is not None and best >= MARGIN:
        bet = "yes" if best == yes else "no"
    return {
        "bet": bet,
        "price": str(ask if bet == "yes" else bid) if bet else None,
        "yes_edge": None if yes is None else str(yes),
        "no_edge": None if no is None else str(no),
        "margin": str(MARGIN),
    }


def collect(
    markets: list[dict[str, Any]],
    listed_at: datetime,
    client: jev.JevClient,
    dataset: Dataset,
    max_new: int | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Summary:
    """Forecast every market in `markets` not already in `dataset`.

    Failures are recorded, not retried, so each market has exactly one record.
    """
    summary = Summary(universe=len(markets))
    done = dataset.keys("ticker")
    with dataset.appender() as out:
        for market in markets:
            ticker = market["ticker"]
            if ticker in done:
                summary.skipped += 1
                continue
            if max_new is not None and summary.forecast + summary.errors >= max_new:
                break
            decided_at = clock()
            record: dict[str, Any] = {
                "ts": decided_at.isoformat(timespec="seconds"),
                "listed_at": listed_at.isoformat(timespec="seconds"),
                "ticker": ticker,
                "event_ticker": market.get("event_ticker"),
                "close_time": market.get("close_time"),
                "quote": {k: market.get(k) for k in QUOTE_FIELDS},
                "question_version": QUESTION_VERSION,
                "model_requested": client.model,
                "prereg_sha256": PREREG_SHA256,
            }
            try:
                result = client.ask(state_text(market, decided_at), {"resolve_yes": QUESTION})
            except jev.SpendCapExceeded as exc:
                summary.stopped = f"spend cap: {exc}"
                break
            except Exception as exc:  # noqa: BLE001 - a failed forecast is recorded, not fatal
                summary.errors += 1
                out.write({**record, "error": f"{type(exc).__name__}: {exc}"})
                continue
            p = result.prob("resolve_yes")
            bid = _price(market.get("yes_bid_dollars"))
            ask = _price(market.get("yes_ask_dollars"))
            decision = decide(Decimal(str(p)), bid, ask)
            summary.forecast += 1
            summary.bets += decision["bet"] is not None
            out.write(
                {
                    **record,
                    "model": result.model,
                    "p_yes": p,
                    "input_tokens": result.input_tokens,
                    "decision": decision,
                }
            )
    summary.spent_usd = str(client.spent_usd)
    return summary


def run(
    out_dir: Path,
    log: Callable[[str], None] = print,
    max_new: int | None = None,
    cap_usd: Decimal | str = CAP_USD,
    data: Any = None,
    client: jev.JevClient | None = None,
) -> dict[str, Any]:
    """Fetch the live Kalshi listing and forecast its popular markets not yet in `forecasts.jsonl`.

    Calls the Kalshi API and the paid Jev API. Writes `forecasts.jsonl` and `cost.jsonl` in
    `out_dir`. `max_new` caps the markets tried this run.
    """
    from ..data.kalshi import KalshiData

    data = data or KalshiData(per_second=10)
    client = client or jev.JevClient(
        model=MODEL, cap_usd=cap_usd, log_path=out_dir / "cost.jsonl", purpose=NAME
    )
    listed_at = datetime.now(UTC)
    markets = popular(data.open_markets(limit_pages=None), MIN_VOLUME_24H, TOP)
    summary = collect(markets, listed_at, client, Dataset(out_dir / "forecasts.jsonl"), max_new)
    log(f"jev forecasts: {asdict(summary)}")
    return {"model": MODEL, "question_version": QUESTION_VERSION, **asdict(summary)}


COLLECTOR = Collector(
    name=NAME,
    description="Jev's probability that each popular Kalshi market resolves YES, with the quote",
    run=run,
    used_by=(EXPERIMENT,),
)

"""Exchange fee models. Verify against the sources below whenever a schedule is updated.

Kalshi: https://kalshi.com/docs/kalshi-fee-schedule.pdf (7 Jul 2026 update)
  taker = roundup(M * 0.07 * C * P * (1 - P)); maker = roundup(M * 0.0175 * C * P * (1 - P)),
  with the maker multiplier 0 unless the series charges maker fees. A series exposes this through
  `fee_type` and `fee_multiplier`.
Kalshi rounding: https://docs.kalshi.com/getting_started/fee_rounding.md (read 2026-11-11)
  Each fill's fee is rounded up to $0.000001, and an accumulator per order brings the total to what
  one equivalent fill would cost. Balances move on a $0.0001 grid for direct members, such as an API
  account, and on a $0.01 grid for non-direct members. Demo fills on 2026-11-11 matched the formula
  rounded up to $0.0001 on every fill, including a 0.42-contract fill.

`kalshi_fee` defaults to the $0.01 grid, because the committed E001-E003 analyses and the E003
preregistration use it. Trading and simulation of a direct account pass `KALSHI_DIRECT_INCREMENT`.
Polymarket: https://docs.polymarket.com/trading/fees
  taker = C * rate * P * (1 - P); makers are never charged.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, Decimal

D = Decimal
KALSHI_TAKER = D("0.07")
KALSHI_MAKER = D("0.0175")
KALSHI_CENT = D("0.01")
KALSHI_DIRECT_INCREMENT = D("0.0001")
POLYMARKET_TAKER_RATES = {
    "crypto": D("0.07"),
    "sports": D("0.05"),
    "finance": D("0.04"),
    "politics": D("0.04"),
    "economics": D("0.05"),
    "culture": D("0.05"),
    "weather": D("0.05"),
    "other": D("0.05"),
    "mentions": D("0.04"),
    "tech": D("0.04"),
    "geopolitics": D("0"),
    "combos": D("0.05"),
}


def kalshi_fee(
    count: Decimal,
    price: Decimal,
    is_taker: bool = True,
    fee_type: str = "quadratic",
    multiplier: Decimal = Decimal(1),
    increment: Decimal = KALSHI_CENT,
) -> Decimal:
    if is_taker:
        rate = KALSHI_TAKER
    elif fee_type == "quadratic_with_maker_fees":
        rate = KALSHI_MAKER
    else:
        return D(0)
    raw = multiplier * rate * count * price * (1 - price)
    return (raw / increment).to_integral_value(rounding=ROUND_CEILING) * increment


def polymarket_fee(count: Decimal, price: Decimal, category: str, is_taker: bool = True) -> Decimal:
    if not is_taker:
        return D(0)
    return count * POLYMARKET_TAKER_RATES[category] * price * (1 - price)

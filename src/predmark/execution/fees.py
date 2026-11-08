"""Exchange fee models. Verify against the sources below whenever a schedule is updated.

Kalshi: https://kalshi.com/docs/kalshi-fee-schedule.pdf (7 Jul 2026 update)
  taker = roundup(M * 0.07 * C * P * (1 - P)); maker = roundup(M * 0.0175 * C * P * (1 - P)),
  with the maker multiplier 0 unless the series charges maker fees. A series exposes this through
  `fee_type` and `fee_multiplier`. Non-direct members settle on a $0.01 grid.
Polymarket: https://docs.polymarket.com/trading/fees
  taker = C * rate * P * (1 - P); makers are never charged.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, Decimal

D = Decimal
KALSHI_TAKER = D("0.07")
KALSHI_MAKER = D("0.0175")
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
    increment: Decimal = Decimal("0.01"),
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

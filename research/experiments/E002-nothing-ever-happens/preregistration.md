# E002: Nothing Ever Happens. What does buying NO on every market earn?

Registered 2026-11-07, before any outcome data for this experiment was pulled. Metadata inspected
first: page counts and close dates for the market listings. E001 was completed beforehand and used
overlapping markets through 2026-10-31, so its results were known when this was designed, including a
39% YES rate in its sample. The holdout below begins after E001's data ends for that reason.

## Strategy

As soon as a market has a bid, buy NO at the executable price `1 - yes_bid`, pay the taker fee, and hold
to settlement. There is no selection on price, spread, volume, or topic.

## Population

Kalshi binary non-combo markets with result `yes` or `no`, in every category and frequency, with close
time from 2025-01-01 through 2026-11-05 UTC. Combo markets are excluded by design.

## Sampling

The population is too large to list in full, so sampling has two stages, using SHA-256 of `E002:<id>`:

1. The 1,500 series with the lowest hash, out of all series on the exchange.
2. From the pooled listings of those series, events in ascending hash order, taking every market in an
   event, until the split's candlestick budget is reached: 10,000 requests for dev, 6,000 for holdout.

Markets that closed in the window but have not settled are counted and reported, not imputed.

## Entry

The entry is anchored to `open_time`, which is known in advance, so an early close cannot leak the
outcome. Use the earliest hourly candle ending after `open_time`, no more than 24 hours after it, and
strictly before `close_time`, whose closing `yes_bid` is above 0. A market with no such candle is not
traded, and the count is reported. Markets open for under an hour, such as 15-minute crypto contracts,
are therefore excluded by construction.

## Split

- **Dev:** closes before 2026-11-01. Overlaps data seen in E001.
- **Holdout:** closes 2026-11-01 through 2026-11-05. No overlap with E001.

An event belongs to the split of its latest close. Each split is evaluated once with `analyze.py`.

## Hypothesis, evaluated on the holdout

**H1.** Mean net profit per contract is above 0, meaning the 95% event-clustered bootstrap interval lies
entirely above 0. The expected result is no: at fair prices a taker loses about half the spread plus the
fee on every trade.

## Costs

`net = (1 - y) - (1 - yes_bid) - fee`, with the Kalshi taker fee on 100-contract lots per contract, using
each series' current `fee_multiplier`. Historical fee changes and displayed depth are not modelled, so
capacity is not assessed. Unit size is one contract per market, so results are equal-weighted by market.

## Uncertainty

2,000 bootstrap resamples of whole events, seed 0. As a sensitivity check, also resample whole series.
Date clustering is reported for dev only, since the holdout has just 5 dates. H1 is decided by the event
interval. If the series interval disagrees, the finding is reported as not robust.

## Reported descriptively, with no hypothesis

Gross profit before costs, return on capital (total net divided by total NO cost), NO win rate, results
by category, by NO entry price bucket, and by number of markets in the event. As a control, the mirror
strategy of buying YES at the ask (where `yes_ask < 1`).

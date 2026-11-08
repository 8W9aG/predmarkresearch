# E001: Are Kalshi prices calibrated at executable quotes, and is any miscalibration tradable?

Registered 2026-11-06, before any outcome data for this population was pulled or inspected. Metadata
inspected beforehand: API schemas, series counts per category, and field values for a handful of
markets. Those looks motivated the early-close rule below.

## Why this study

Calibration studies often score last-traded prices. A trade price is not something a strategy can
act on at a chosen time, and it exists only when someone traded. This study scores two-sided
**quotes** at a fixed horizon and evaluates rules at the price actually payable: the ask or bid,
plus the taker fee.

## Population

Binary, non-combo Kalshi markets with result `yes` or `no`, where the series has:

- category in Economics, Climate and Weather, Financials, Crypto, Commodities
- frequency in daily, weekly, monthly
- close time from 2025-01-01 through 2026-10-31 UTC

## Sampling

Deterministic, by SHA-256 hash of `E001:<event_ticker>` and `E001:<market_ticker>`:

- at most 40 events per series, lowest hash first
- at most 6 markets per event, lowest hash first
- ordered by (rank of event within series, hash), then truncated to 6,000 candlestick requests

## Observation

The observation time is 12 hours before `close_time`, using the most recent hourly candle ending at
or before that time and no more than 3 hours earlier. Price is the candle's closing `yes_bid` and
`yes_ask`; `mid` is their average.

## Filters, all knowable at observation time except the first

1. **Scheduled close.** `close_time` must be within 1 hour of the series' modal UTC close hour
   (circular distance). Nearly all markets are flagged `can_close_early`, so an unscheduled early
   close would place the observation closer to the resolving event, leaking the outcome. This filter
   uses the realized close time. It is an imperfect guard, and it is disclosed as such.
2. Series with fewer than 5 sampled markets are excluded, because a modal hour is not meaningful.
3. Two-sided quote: `yes_bid > 0` and `yes_ask < 1`.
4. Spread `yes_ask - yes_bid` of at most 0.10.

No filter uses lifetime volume, which would condition on the future.

## Split

Events sorted by close time, then by event ticker. The first 70% are dev, the last 30% are holdout,
and no event spans both. Both splits are evaluated exactly once with the code in `analyze.py`.

## Hypotheses, evaluated on the holdout

- **H1 (calibration).** In `logit P(yes) = a + b logit(mid)`, the 95% event-cluster bootstrap interval
  for `b` excludes 1. No direction is specified.
- **H2a (fade longshots).** For `mid <= 0.15`, buy NO at `1 - yes_bid` paying the taker fee. Mean net
  profit per contract has a 97.5% interval entirely above 0.
- **H2b (back favorites).** For `mid >= 0.85`, buy YES at `yes_ask` paying the taker fee. Mean net
  profit per contract has a 97.5% interval entirely above 0.

The 97.5% level is the Bonferroni correction for the two trading rules. The expected result for H2a
and H2b is no: a rule would have to clear spread and fee on a heavily traded venue.

## Costs

Taker fee is `roundup(M * 0.07 * C * P * (1 - P))` to the cent with `C = 100` contracts, where `M` is
the series `fee_multiplier` observed at data collection, per contract. Today's fee parameters are
applied to the whole sample; historical fee changes are not modelled. Displayed size at the quote is
unavailable in candles, so capacity is not assessed. Rejecting the null therefore would not establish
a deployable strategy.

## Uncertainty

Intervals use 2,000 bootstrap resamples of whole events, seed 0. A sensitivity analysis clusters by
close date instead, since events closing the same day can share a shock. Hypotheses are decided by
the event-clustered interval and the date-clustered interval is reported next to it. If the two
disagree, the finding is reported as not robust.

## Reported descriptively, with no hypothesis

Brier score, log loss, expected calibration error, ten-bin table, and per-category recalibration.

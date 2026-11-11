# E003: Jev forecasts. Would betting where Jev disagrees with the price earn money after costs?

Registered 2026-11-08, before any forecast was collected and before any outcome data for this
experiment existed. Inspected first: the Kalshi open-market listing schema, the TypeSafe API schema,
one-question test calls to Jev (`ping`), and a 50-market pilot on 2026-11-08 that asked Jev about the
rules of markets (early close, objective source, category), not about outcomes. The pilot was dropped
and its labels are not used. No forecasts of outcomes and no outcomes were seen. E002 (Nothing Ever
Happens) was completed beforehand: taking the quote on every market lost about 18 cents per contract,
almost all of it spread.

## Why forward only

Jev's training cutoff is unknown, so forecasts of markets that have already resolved could score
outcomes the model memorised. Every forecast in this experiment is collected live, on a market that has
not yet resolved, and recorded with its timestamp and quote before the outcome is known.

## Data collection

The `jev-forecasts` collector (`src/predmark/collect/jev_forecasts.py`) runs in the daily Modal job at
14:00 UTC and writes `collect/jev-forecasts/forecasts.jsonl` on the `predmark-paper` volume. It never
trades.

- **Universe:** open Kalshi non-combo markets with listing 24-hour volume of at least 10,000
  contracts, busiest first, at most 1,000 (the same as Nothing Ever Happens). This is the listing at
  run time, so it is known at decision time.
- **Forecast:** each market once, on the first run that finds it in the universe. Jev `jev-1.14.0` is
  asked one `noul` question, `resolve-yes-v1` ("Will this market resolve YES?"), about a text with the
  decision time, title, YES outcome, scheduled close time and rules. Jev is not shown prices or
  volumes. `p` is its probability of YES.
- **Quote:** the listing's YES bid and ask, their sizes, the last price, 24-hour volume and open
  interest, from the same listing that chose the universe. A side quoted at 0 or 1 is treated as
  empty.
- **Decision:** with `fee(x)` the Kalshi taker fee on one contract at price `x` (multiplier 1, rounded
  up to the cent):
  - YES edge `= p - ask - fee(ask)`, NO edge `= bid - p - fee(bid)`.
  - If the larger edge is at least 0.05, the decision is to bet that side for one contract: buy YES at
    the ask, or buy NO at `1 - bid`. Otherwise the decision is no bet.

  The decision is recorded with both edges. No order is placed.

Failed Jev calls are recorded as errors and not retried, so each market has one record. The model,
question, margin and universe are fixed. Changing any of them is a new experiment on later data.

## Split

By forecast time (UTC):

- **Dev:** 2026-11-08 through 2026-11-21. Used freely to check the pipeline and the analysis.
- **Holdout:** 2026-11-22 through 2027-02-21.

The holdout sample is holdout forecasts whose market has a `yes` or `no` result by 2027-03-07. It is
evaluated once, on or after 2027-03-07, with `analyze.py`, written and tested on synthetic data with a
planted effect before then. Markets still open, voided or unresolved on that date are counted and
reported, not imputed. This favours markets with short horizons, which is reported with the result.

## Hypotheses, evaluated on the holdout

**H1 (profit).** Mean net profit per recorded bet decision is above 0: the 95% event-clustered
bootstrap interval lies entirely above 0. With fewer than 100 settled holdout bets, H1 is reported as
not supported for lack of power. The expected result is no, since E002 found the spread alone costs
takers most of what a modest edge would earn.

**H2 (information).** Jev's forecast carries information beyond the price. On every holdout forecast
with both a bid and an ask, fit a logistic regression of the outcome on `logit(mid)` and `logit(p)`,
where `mid = (bid + ask) / 2`. H2 is supported if the 95% event-clustered bootstrap interval of the
`logit(p)` coefficient lies entirely above 0. H2 can hold while H1 fails, if the information is too
small to pay for the spread.

## Costs

`net = y - ask - fee(ask)` for a YES bet and `net = (1 - y) - (1 - bid) - fee(bid)` for a NO bet, where
`y` is 1 if the market resolved YES, at the recorded quote. As a sensitivity check, recompute with each
series' current `fee_multiplier`. Jev's cost (about $0.00001 per forecast) is reported separately and
is not netted from per-contract profit.

The bets are hypothetical. The listing quote can be up to a few minutes old by the time of the
forecast, and only the displayed size at the top of the book is known, so the result says nothing
about capacity beyond one contract and may be optimistic about fills.

## Uncertainty

2,000 bootstrap resamples of whole events, seed 0. As a sensitivity check, also resample whole series.
H1 and H2 are decided by the event interval. If the series interval disagrees, the finding is reported
as not robust.

## Reported descriptively, with no hypothesis

Calibration of `p` (reliability table, Brier score, log loss) next to the same for `mid`, on all
forecasts. Bets by side and by entry price bucket. Net results by category, and at margins of 0, 0.02
and 0.10 alongside the preregistered 0.05. The bet rate, the Jev error rate, and total Jev spend. The
model ID that answered each forecast, which should always be `jev-1.14.0`.

## What could still be wrong

- Rules text is untrusted. Text planted in rules could bias Jev's forecast. Each record keeps the
  ticker needed to look up its inputs and check outliers.
- The universe is chosen by volume, which is related to topic and horizon. Results hold for this
  universe only.
- Markets in one event are not independent, which is why uncertainty is clustered by event.

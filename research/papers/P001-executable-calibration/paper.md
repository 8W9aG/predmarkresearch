# Calibration of Kalshi Prices at Executable Quotes: A Preregistered Null Result

**Status: Draft, AI-generated, not peer reviewed.** All results are from experiment E001.

## Abstract

We test whether Kalshi prices are calibrated, and whether simple rules profit from any miscalibration,
using two-sided quotes 12 hours before scheduled close rather than last-traded prices. Trading rules
are charged the full spread and the taker fee. In a preregistered holdout of 445 markets in 160 events
from one month, the logistic calibration slope is 1.096 (95% interval 0.889 to 1.451), which does not
differ detectably from 1. Fading longshots earns -$0.0301 per contract (97.5% interval -$0.0821 to
+$0.0104), and backing favorites earns -$0.0026 (-$0.0447 to +$0.0306). We find no evidence of a
tradable edge from these rules, although the sample is too small to rule out a small one.

## 1. Introduction

A calibration result only matters to a trader if it holds at prices that could have been traded at a
time chosen in advance. A last-traded price may be stale, and it exists only when someone traded. We
therefore score quotes at a fixed horizon and charge rules what a taker would actually pay.

Our contribution is modest: a quote-based evaluation design with costs included, plus a preregistered
null result. Related work has not yet been reviewed for this draft, so we make no claim about how this
design compares with earlier studies.

## 2. Data

The population is binary, non-combo Kalshi markets from daily, weekly, and monthly series in Economics,
Climate and Weather, Financials, Crypto, and Commodities, closing from 2025-01-01 through 2026-10-31.
We drew a deterministic hash-based sample of 6,000 markets, with at most 40 events per series and 6
markets per event. We used the closing bid and ask of the latest hourly candle at least 12 hours
before close and no more than 3 hours older than that.

| Step | Markets left |
| --- | --- |
| Sampled candidates | 6,000 |
| Series with at least 5 sampled markets | 5,893 |
| Scheduled close (within 1 hour of the series' modal close hour) | 5,573 |
| Quote found | 3,998 |
| Two-sided quote | 2,355 |
| Spread at most $0.10 | 1,454 |

Seven candidates had request errors. The scheduled-close filter guards against leakage: nearly all
markets can close early, and an early close would place our observation closer to the outcome.

Events were split chronologically into dev (first 70%: 1,009 markets, 371 events) and holdout (last
30%: 445 markets, 160 events, closing 2026-09-28 to 2026-10-31).

## 3. Method

Calibration is measured by fitting `logit P(yes) = a + b logit(mid)`, where `b = 1` is well calibrated.
Two rules were tested: for `mid <= 0.15`, buy NO at `1 - bid`; for `mid >= 0.85`, buy YES at the ask.
Both pay the Kalshi taker fee, computed on 100-contract lots using current series fee parameters.

Intervals use 2,000 bootstrap resamples of whole events, since markets within an event are dependent.
Trading rules use 97.5% intervals, the Bonferroni correction for two rules. We also report intervals
clustered by close date. Hypotheses were fixed in advance, and the holdout was evaluated once.

## 4. Results

| | Dev | Holdout |
| --- | --- | --- |
| Brier score | 0.1093 | 0.0854 |
| Log loss | 0.3458 | 0.2856 |
| Expected calibration error | 0.0293 | 0.0291 |
| Mean spread | $0.0387 | $0.0532 |
| Slope `b` | 1.106 | 1.096 |
| Slope, 95% interval by event | 0.983 to 1.259 | 0.889 to 1.451 |
| Slope, 95% interval by date | 0.970 to 1.258 | 0.912 to 1.424 |
| Fade longshots, mean net (n) | -$0.0024 (380) | -$0.0301 (203) |
| Fade, 97.5% interval by event | -$0.0257 to +$0.0193 | -$0.0821 to +$0.0104 |
| Fade, 97.5% interval by date | -$0.0248 to +$0.0179 | -$0.0721 to +$0.0049 |
| Back favorites, mean net (n) | -$0.0087 (208) | -$0.0026 (113) |
| Back, 97.5% interval by event | -$0.0486 to +$0.0253 | -$0.0447 to +$0.0306 |
| Back, 97.5% interval by date | -$0.0505 to +$0.0239 | -$0.0374 to +$0.0283 |

None of the preregistered hypotheses is supported on the holdout.

Exploratory observation, not a test: the slope point estimate is above 1 in both splits, which would
mean prices are slightly under-confident. Neither interval excludes 1.

## 5. Limitations

The holdout spans one month and 30 close dates, so it reflects one period. Filters removed 76% of
candidates, so results apply to markets with tight two-sided quotes at the horizon. The sample is
weighted to recent months. The scheduled-close filter relies on realized close times and may miss some
early closes. Current fee parameters were applied to the whole sample. Candles do not report quoted
size, so capacity was not assessed. Finally, the preregistration was not committed to version control
before analysis, so its timing is not independently verifiable.

## 6. Conclusion

At executable quotes, in this sample, we find no detectable miscalibration and no evidence that either
rule profits as a taker. The intervals leave room for small effects, so larger samples and longer
holdouts are the natural next step, along with strategies that earn the spread rather than pay it.

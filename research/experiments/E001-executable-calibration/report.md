# E001 report

**Decision: none of the three hypotheses was supported on the holdout.** All numbers are from
`results.json`.

| Hypothesis | Holdout estimate | Event-clustered interval | Date-clustered interval | Supported |
| --- | --- | --- | --- | --- |
| H1: calibration slope differs from 1 | 1.096 | 95%: 0.889 to 1.451 | 95%: 0.912 to 1.424 | No |
| H2a: fade longshots, net per contract | -$0.0301 | 97.5%: -$0.0821 to +$0.0104 | 97.5%: -$0.0721 to +$0.0049 | No |
| H2b: back favorites, net per contract | -$0.0026 | 97.5%: -$0.0447 to +$0.0306 | 97.5%: -$0.0374 to +$0.0283 | No |

This is a failure to find an effect, and it does not show that none exists. The slope interval is wide
enough to include moderate miscalibration, and the H2a interval still allows about +$0.01 per contract.

## Sample

| Step | Markets left |
| --- | --- |
| Sampled candidates (7 had request errors) | 6,000 |
| Series with at least 5 sampled markets | 5,893 |
| Scheduled close | 5,573 |
| Quote found at T-12h | 3,998 |
| Two-sided quote | 2,355 |
| Spread at most $0.10 | 1,454 |

Dev has 1,009 markets in 371 events. Holdout has 445 markets in 160 events.

## What could still be wrong

- The holdout covers one month (2026-09-28 to 2026-10-31, 30 close dates), so it reflects one regime.
- 76% of candidates were filtered out, so results describe markets with tight two-sided quotes.
- The sample is weighted to recent months, with 412 of 1,454 markets closing in October 2026.
- The scheduled-close filter uses realized close times and may miss some early closes.
- Current fee parameters were applied to all periods. Quoted size is unknown, so capacity is untested.
- The preregistration was not committed to git before the analysis ran. Its hash is in the trial
  ledger, but that is written by the run itself and is not independent evidence of its timing.
- See `amendments.md` for the two collection fixes made before analysis.

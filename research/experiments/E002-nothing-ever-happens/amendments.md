# E002 amendments

Kept apart from `preregistration.md` so its hash stays valid. Everything here was written after the
download and before any outcome was examined. The checks read entry quotes and metadata, not `result`.

## 2026-11-07: data checks before opening the holdout

**Correction to the preregistration.** It says the holdout has no overlap with E001. That is not quite
true. The registered rule assigns an event by its latest close, so 488 of the 6,000 holdout markets
(7 events, 40 of them tradable) closed before 2026-11-01, some as early as 2025-09-01. H1 is still
decided on the split as registered.

**Added, descriptive only.** `holdout_strict_window` repeats the evaluation on markets that closed
2026-11-01 through 2026-11-05. `by_series` reports each series with at least 100 traded markets,
because one series, KXSILVERH, is 1,008 of the 3,606 tradable holdout markets. Neither affects H1.

**Checked, no change needed.**
- 16,000 unique markets: 10,000 dev, 6,000 holdout. No series failed to list.
- 559 markets in the sampled series closed in the window without settling. They are not imputed.
- 2 candle requests failed with 404, both IPO markets in the holdout. They count as not traded.
- 31 tickers contain "MVE" but are not combos. They are Serie A markets such as Como vs Venezia.
- All sampled series have a fee multiplier of 1. No entry has a crossed book or a missing ask.
- Most empty candle responses are markets open under an hour, which the registration excludes
  (336 of 376 in the holdout, 171 of 271 in dev). KX30YRRATEH accounts for most of them.
- Archived and live candle fields both parse: `close` and `close_dollars`.

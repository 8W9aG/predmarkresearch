# Research ideas

Candidates for `/research-cycle`. Each needs new data collected after the holdout it builds on.

1. **Maker-side rules.** E001 charged takers a mean holdout spread of $0.0532. Test whether resting
   quotes earn that spread net of adverse selection. Needs: recorded books and trades.
   Path to profit: earn the spread and avoid the taker fee.
2. **Longer calibration holdout.** The E001 slope was above 1 in both splits, but neither interval
   excluded 1. Register a new test on markets closing after 2026-10-31, with a larger sample.
   Path to profit: only if the effect clears costs, which E001 did not show.
3. **Quoted depth and capacity.** Use `record-books` to measure the size available at the quote.
   Needs: weeks of continuous recording. Path to profit: tells us which rules can hold real size.
4. **Kalshi and Polymarket price gaps.** Measure quote gaps on matched contracts, net of both fee
   schedules. Polymarket data only, since orders there are unavailable from the US. Path to profit:
   a Polymarket lead could inform Kalshi trades.

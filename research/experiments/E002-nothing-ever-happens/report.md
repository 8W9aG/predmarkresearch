# E002 report: Nothing Ever Happens

**Result: H1 not supported.** Buying NO on every market as soon as it has a bid loses money. On the
holdout it lost 17.6 cents per contract, 95% event-clustered interval −20.9 to −14.5 cents. The
series-clustered interval agrees (−21.9 to −11.4). The expected result was no.

All numbers are from `results.json` (dataset sha256 `f8f15eec…c417`, prereg sha256 `cbeaba8f…290a`).

## Process

- The preregistration, amendments, and analysis code were committed (`8c5a598`) before the holdout
  was opened. The analysis ran once, and both splits are logged in `research/trials.jsonl`.
- The data checks done before opening, and one correction to the registration, are in `amendments.md`.

## Results

|                               | Dev (closes 2025-01-06 to 2026-10-31) | Holdout (as registered) |
| ----------------------------- | ------------------------------------- | ----------------------- |
| Markets traded / events       | 7,196 / 1,244                         | 3,606 / 407             |
| Mean NO entry price           | 0.752                                 | 0.707                   |
| NO win rate                   | 54.4%                                 | 53.7%                   |
| Gross per contract            | −20.8¢                                | −17.0¢                  |
| Net per contract (after fees) | −21.4¢ (−23.8 to −19.1)               | −17.6¢ (−20.9 to −14.5) |
| Return on capital             | −28.5%                                | −24.9%                  |
| Control: buy YES at the ask   | −23.9¢ (−27.0 to −20.7)               | −20.1¢ (−23.8 to −16.7) |

Restricting the holdout to markets that closed from 1 to 5 November 2026 changes almost nothing:
3,566 markets, −17.7¢ (−21.1 to −14.6).

## Why it loses

The fee is under 1 cent per contract on 100-lots. Nearly the whole loss is the spread at entry.
In a market's first hours, the median spread was 24 cents in dev and 15 cents in the holdout. NO at
`1 − yes_bid` is the NO ask, so the strategy pays the far side of a wide market. Buying YES at the
ask loses about as much, which shows that the loss comes from crossing the spread, not from favouring
NO. NO did not win more often than its price implied: it won 54% of the time at an average price of
71 to 75 cents.

The only holdout groups with a mean above zero were small, and all were near zero: Elections (123
markets, +2.6¢), Economics (91, +1.6¢), and KXMIDTERMMOV (100 markets, 26 events, +5.9¢). These
are descriptive. No hypothesis was registered for them, and none of them is a finding.

## Limitations

- The holdout covers only 5 close dates. Weekly NFL and NBA markets and hourly silver are a large share.
  KXSILVERH alone is 1,008 of the 3,606 traded holdout markets.
- Entry uses hourly candle closes, not a live order book, so depth is not modelled. Displayed sizes
  are often below one contract, so capacity is not assessed.
- Fees use each series' current multiplier. Historical fee changes are not modelled.
- Markets open for under an hour, and combo markets, are excluded. 5,198 of 16,000 sampled markets
  never had an eligible quote with a bid, so the result covers markets that were tradable.
- The dev split overlaps E001's data, so its estimates are not independent of that work.

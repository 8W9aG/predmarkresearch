# Adaptive Auto-Harness

**Citation.** Zewen Liu, Zhan Shi, Yisi Sang, Bing He, Minhua Lin, Tianxin Wei, Dakuo Wang, Benoit Dumoulin, Wei Jin and Hanqing Lu. "Adaptive Auto-Harness: Sustained Self-Improvement for Agentic System Deployment on Open-Ended Task Streams". arXiv 2606.01770, v3, 1 November 2026. [Paper](https://arxiv.org/abs/2606.01770), [code](https://github.com/A-EVO-Lab/a-evolve). Library key `liu2026-8a82`.

**Status.** Not applicable as a source of a trading edge. It is a preprint on agent engineering, not peer reviewed, and prediction markets are one of its three test streams. Three habits carry over to this repo (see *Use in this repo*).

**Read.** The HTML full text of v3, on 2026-11-11. v1 and v2 were not read, and v3 is a large revision of them. The body and Appendices A to H.2 were read in full. Appendices H.3, I and J (artifact selection, human-in-the-loop, prompts) were read only as a summary, so this page makes no claim from them. The code and the per-task results were not read, and the per-task results are not public yet ("will be released upon acceptance"). Calculations marked *ours* are not in the paper.

## Summary

**Claim.** An "auto-harness" changes an LLM agent's prompts, skills, memory and tools from execution feedback, with the model weights fixed. The paper says that gains from this can last on a stream of tasks whose mix changes over time. The authors' system does this best when it keeps a workspace for the evolver separate from the harness that it deploys.

**Method.**

- *System.* The solver is Claude Sonnet 4.6 and the evolver is Claude Opus 4.6, both at temperature 0. The evolver has four roles: an analyst ranks failures, three researchers test hypotheses, a builder changes the harness and a verifier checks it. The evolver's workspace holds a task board, research logs and tests. It is kept apart from the prompts, skills and tools that the solver uses.
- *Feedback.* A "temporal-reveal gate" holds back each task's score until the task resolves. The authors say that this is "a channel-level control, not an information-flow guarantee".
- *Comparisons.* Ports of five earlier systems (A-Evolve, GEPA, Meta-Harness, Continual Harness, SkillOS), a fixed tool kit (OctoTools), and five fixed LLMs with no evolution. The ports differ from the originals. For example, GEPA has no Pareto selection. Each configuration has one recorded run.
- *Streams.* 5,075 Polymarket tasks from 6 to 22 February 2026, taken from the PolyBench benchmark (library key `cheng2026-4295`, not yet reviewed). Also 503 FutureX event-forecast questions and 261 CTF security challenges.

**The prediction market stream.**

- *Input.* Each task is a frozen snapshot: the order book, news context and market properties such as displayed price, spread, depth and time to resolution. The solver has no live web access.
- *Output.* A decision to buy or sell an outcome, with a confidence from 0 to 1. Confidence below 0.60 means no trade.
- *Order.* Tasks are queued by resolution time, then by snapshot time. The tasks are not in the order that their information became available.
- *Accuracy.* "All-task accuracy" is the share of all 5,075 tasks with a correct trade. A skipped or gated task counts as wrong, so the score is coverage times accuracy on the trades taken.
- *Money.* "Net$" is simulated profit from a nominal $5,000 bankroll. It has no fees and no shared capital limit. The book is walked for size, up to 10 × confidence contracts. The authors say the simulation does "not establish actual fills or achievable trading profit".
- *Market-following control.* It buys the highest-priced outcome on every task, with confidence 1. The paper doesn't say whether the price is the ask or the displayed price.

**Findings, as the paper reports them (Table 2, Table 9).**

| Configuration | Coverage | All-task accuracy (95% CI) | Net$ (95% CI) |
| --- | --- | --- | --- |
| Static Sonnet 4.6 | 31.5% | 22.2 (21.0 to 23.3) | +403 (−11 to +825) |
| Market-following control | 100.0% | 72.0 (70.8 to 73.1) | −5,348 (−6.3k to −4.4k) |
| Continual Harness (port) | 93.3% | 76.9 (75.8 to 78.1) | +693 (+384 to +1,012) |
| Adaptive | 91.4% | 77.4 (76.3 to 78.6) | +949 (+689 to +1,221) |
| Multi-agent | 95.8% | 79.8 (78.8 to 81.0) | +793 (+497 to +1,091) |
| Full system | 97.9% | 80.9 (79.8 to 82.0) | +881 (+511 to +1,261) |

- Full system minus market-following, paired on the same tasks: +8.9 points (7.7 to 10.1). With a block bootstrap over batches: 5.6 to 13.8.
- "Every system is near-perfect on lock markets", where the bought side is priced at 0.9 or more. The authors say that the score is decided on outcomes priced below 0.5.
- Stored instructions grow in all 16 runs, but four runs end below static Sonnet. SkillOS goes from +19.6 to −20.7 points against static Sonnet as its instructions grow by 108% and its answer rate falls from 47.2% to 3.5%.
- A harness that is frozen and replayed on later batches loses more as those batches move away from its training window (r = −0.53 over 74 points, 63 of them from Polymarket).
- On FutureX and CTF, the differences between the best configurations are not significant (unadjusted McNemar p from 0.11 to 0.39).

The paper has no Brier score, log score or calibration curve. Confidence is used only as a gate and to size bets.

## Where the claims go beyond the evidence

These are our observations, not the paper's. The authors disclose some of them in Section 6 and Appendix B, but not in the abstract.

- **The headline gain is mostly coverage.** Static Sonnet trades only 31.5% of tasks. Its accuracy on the trades it takes is 22.2 / 31.5 ≈ 70.5% (ours), against 82.6% for the full system. So most of the gap of 58.7 points between 22.2% and 80.9% comes from trading more often, not from better decisions. The abstract's "versus 22.2%" doesn't say this.
- **Static Sonnet is slightly worse than the market on its own trades.** About 70.5% on its chosen trades (ours), against 72.0% for buying the favourite on every task. The paper says that an always-answer static LLM is "a different, unevaluated control", so it has no comparison at equal coverage.
- **The data can leak outcomes.** The authors write that "snapshot-local news may describe an event before its market settles", and that every row in the archive carries a pass or fail label matched to the outcome. They write that "premature reads and their effects are unresolved". On FutureX, five unrevealed answers reached A-Evolve's evolution context. The paper doesn't compare the training cutoff of Sonnet 4.6 or Opus 4.6 with February 2026. Any leak raises every LLM configuration over the market-following control, which uses no text. This makes the 8.9-point margin an upper bound at best.
- **Accuracy isn't profit.** A 72%-accurate control loses $5,348 at zero fees, and the configuration with the highest Net$ (Adaptive) isn't the most accurate one. Net$ has no fees, no capital limit and fills that the authors don't trust. With no capital limit, the control's loss can be larger than the $5,000 bankroll. The paper also gives a trade filter of confidence minus price ≥ 0.20. Applied to a confidence-1 control, this would skip favourites priced above 0.80, but the paper says that the control "trades everything". It is not clear which rules apply to the control.
- **One run per configuration.** The intervals resample tasks from one recorded history. They say nothing about variation from rerunning the evolver, which is the main random part of the method. Evolver time ranges from 0 to 26.6 hours across configurations (Table 8), so compute is not matched either.
- **Tasks within a market aren't independent.** The bootstrap resamples tasks, and the batch-block version resamples adjacent batches. Neither clusters by market or by event. The paper doesn't say how many distinct markets the 5,075 tasks cover. If one market has several snapshots, the intervals are too narrow.
- **Exploratory tests are reported without correction.** Table 9's p-values are "unadjusted across these exploratory comparisons". The FutureX and CTF differences between the leading systems are not significant, but the text still reads them as wins for the full system.

## Use in this repo

**Not applicable as a trading method.** This repo looks for rules that make money after spread and fees, executed through `Engine`. It studies both Kalshi and Polymarket. Kalshi is the only venue it can trade, and Polymarket is used for data and paper trading only. So a Polymarket result would count as evidence, on paper or as a signal for Kalshi (`research/ideas.md`, item 4). This paper's Polymarket result doesn't count, for three reasons. First, it measures whether a side wins, not what it costs to take it. Second, its stream is replayed from snapshots that can contain outcome news. Third, it has no fees.

**It supports E003's design, with no change needed.** [E003](../../experiments/E003-jev-forecast/preregistration.md) already avoids the paper's three weak points:

- *Leakage.* E003 is forward only: each forecast is made live, before the outcome exists. The paper's replay can't rule out news or training data that contain the result.
- *Scoring.* E003's H2 regresses the outcome on `logit(mid)` and `logit(p)`. This scores probabilities against the price on every market, so coverage can't drive the result. The paper scores side-picking with skips counted as wrong.
- *Costs and clustering.* E003's H1 charges the ask, or `1 − bid`, plus the fee, and it clusters by event. The paper does neither.

**Three habits carry over.** None of them needs new code now.

- *Report coverage beside accuracy, and compare at equal coverage.* Any later study of LLM forecasts, such as a follow-up to E003, should report the share of markets with a bet and compare against the price on that same subset. A rule that bets on a chosen subset can look good from selection alone.
- *Treat market-following as the baseline that matters, scored in money.* The paper's control is right to buy the favourite at the price and is useful because it uses no text. In this repo the same control must be charged the ask and the fee, as E002's control was. Accuracy on its own doesn't tell you this: E002 found that NO won 54% of the time and still lost 17.6¢ per contract.
- *More agent instructions can make results worse.* Our skills (`/research-cycle`, `/lit-review`, `/write-paper`) are an auto-harness that we edit by hand. In the paper, instructions that kept growing made SkillOS refuse almost every task. This is relevant to two `TODO.md` items in *Research integrity*: mining agent transcripts for better skills, and optimising agent prompts with DSPy. Both should measure a skill against a fixed task set before and after a change, rerun more than once, and prune instructions as well as add them.

**Follow-up.** PolyBench (`cheng2026-4295`) is the source of the Polymarket stream and is fifth in the index. Its review should check how snapshots and news were collected, how many markets the tasks cover, and whether its prices are bids, asks or last trades.

**No new idea for `research/ideas.md`.** The paper has no testable rule that could make money after costs on data this repo has.

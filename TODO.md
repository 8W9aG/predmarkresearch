# TODO

Open work, grouped by topic and roughly in priority order within each section. When an item lands, delete it here and record it in `CHANGELOG.md` (see Releases).

Most of this list comes from [8W9aG/openinterpretability](https://github.com/8W9aG/openinterpretability) (read 2026-11-08). It covers that repo's lint, test and CI rules, and the TODO items that apply to software and research in general. Items specific to interpretability were left out. Each item names where it applies here.

Prefer a deterministic check (lint, test, CI job) where one can do the job, and use a skill for what needs judgement. Ratchet a new check in rather than failing all existing code at once.

## Next: use Jev

[Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev) is TypeSafe AI's first "System One" model.
- **What it does:** it takes unstructured text and a set of typed questions (`noul` for yes/no, `choice`, `score`) defined in advance. It answers them all in one query, with a probability or confidence for each.
- **What it can't do:** generate free text. Choices with more than 255 options fall back to a slower two-stage scoring.
- **Speed and cost:** TypeSafe claims 70–500 ms end to end. Input costs $0.042 per 1M tokens, output is free, and the context is 64k tokens per request and 32k per question.
- **Access:** it's in early access directly from TypeSafe (docs.typesafe.ai, with a waitlist), and through [Vercel AI Gateway](https://vercel.com/ai-gateway/models/jev) as `typesafe-ai/jev`.
- **Caveats:** TypeSafe says the outputs are calibrated, but the post shows no calibration numbers. Its evals score Jev against the average of large LLMs, not ground-truth labels. So we measure calibration ourselves before relying on it. All figures as of 2026-11-08.

Jev is used for one thing only: daily forecasts of the top markets, collected going forward for E003 (`research/experiments/E003-jev-forecast/preregistration.md`). Not for code, lint, literature review or anything else. Its training cutoff is unknown, so no backfill: only forecasts made before an outcome is known count.

- [ ] From 2026-11-22, check the dev forecasts (2026-11-08 to 2026-11-21): `fetch.py dev`, then `analyze.py dev`. Look at the error rate (2 errors on 2026-11-10), bet rate, spend, and that every record has `model` `jev-1.14.0`.
- [ ] On or after 2027-03-07, run `fetch.py holdout` and `analyze.py holdout` once, and write the report. Both scripts refuse earlier dates.

## This repo's own gaps

Found while building the paper runner, E002 and the website (2026-11-08).

### Trustworthy results

- [ ] Publish the raw data behind each result. `data/` is gitignored, so nobody else can reproduce E001 or E002 from the repo. Each `results.json` records a dataset hash but not where the data lives. Push each dataset to a Hugging Face dataset or a bucket, pinned by that hash, and link it from the report.
- [ ] Preregister the popular-markets version of Nothing Ever Happens as E004. The exploratory backtest (−4.5¢ per contract, 95% interval −8.9¢ to +0.3¢, 182 markets) reused E002's already-opened holdout. It needs its own preregistration and a holdout of markets that close after the preregistration is committed.

### Paper results that match backtests

- [ ] Make the backtest and live entry rules the same. Live entries happen when a market first reaches the top 1,000 by volume, often days after it opens. The backtest enters within 24 hours of opening. Once the listing snapshots exist, run the backtest on the live rule.
- [ ] Compare live paper results with the backtest's prediction per strategy, on the website: per-contract net, NO win rate and entry price. Alert when they diverge by more than noise.

### Execution engine

- [ ] Run `KalshiVenue` against Kalshi's demo exchange. It has only been tested with mocks, and the `kalshi-demo-check` command went when the CLI was narrowed.
  - Add demo as a venue option in the strategy runner.
  - Test the full order lifecycle: place, fill, cancel, and the `reconcile()` check after a crash.
  - Real money stays off (`CLAUDE.md`).
- [ ] Look up fees per series. Paper fills charge every market a fee multiplier of 1, but Kalshi publishes `fee_type` and `fee_multiplier` per series. Use them in `PaperVenue` and the backtest.
- [ ] Handle voided and cancelled markets. A held market that settles without a yes or no result goes into `unresolved` and stays open forever, which inflates capital at risk. Settle it at the venue's terms and report it.
- [ ] Stop overlapping Modal runs. The scheduled `daily` and the manual `run` function can write the same journal at once. Take a lock file on the volume. Also delete the orphaned `nothing_ever_happens.sqlite` from the cancelled first run.
- [ ] Back up the paper journals. All paper history lives in one SQLite file per strategy on one Modal Volume. Snapshot each journal daily, for example to the bucket in "Publish the raw data".

### Security

- [ ] Limit what the GitHub Modal token can reach. The `github-pages` token can access the whole Modal workspace, and anyone who can edit the site workflow could use it. Either move the paper app to its own Modal workspace, or have the daily Modal job publish the run logs itself and give GitHub no Modal token at all.

### Research backlog

- [ ] Start the literature review with the user. 0 of 397 indexed records have been reviewed (`python -m predmark.lit next`). Paper P001 has no citations because of this.
- [ ] Preregister maker-side entry as the next experiment, from `research/ideas.md`. E002 showed the loss is almost all spread, and resting at the bid instead of crossing it is the direct counter. Queue position is unknown, so the fill model must be conservative: fill only when the market trades through the price.

### Housekeeping

- [ ] Delete the local `e002-nothing-ever-happens` branch, which now duplicates `main`.

## Rules openinterpretability enforces that we don't yet

### Lint

- [ ] Widen ruff's rule set to `["E", "F", "I", "B", "UP", "SIM", "D", "ERA"]`. Today ours is `E F I B UP`.
  - `SIM` simplifies code, `D` checks docstrings (Google convention) and `ERA` rejects commented-out code.
  - Ignore `D105` and `D107`, because the class docstring covers magic methods and `__init__`.
  - Exempt `tests/**` from `D1`, because a test's name says what it checks.
- [ ] Measure docstring coverage with `interrogate` on `src/predmark`, with `fail-under` set at today's level. Raise the threshold as coverage rises, and never lower it. Skip magic, `__init__`, private and semi-private names.
- [ ] Add a size limit: 500 lines per file and 40 lines per function. Count the signature as one line, and leave decorators, parameter lists and the docstring out of the count.
  - A function that reads better whole gets `# long-function: <reason>` on its `def` line.
  - Exempt tests, config, lockfiles, generated data (`research/literature/library.jsonl`, `results.json`) and vendored code.
  - Port `scripts/check_line_limit.py`.
- [ ] Lint hand-written Markdown with `pymarkdownlnt`. Use their config: allow one paragraph per line (MD013 off), sibling-only duplicate headings (MD024) and YAML front matter.
  - Also check relative links and heading anchors (port `scripts/check_markdown.py`).
  - Exempt generated Markdown such as `research/literature/INDEX.md`.
- [ ] Lint the prose in comments and docstrings with Vale against ASD-STE100 rules (`.vale.ini` and `.vale/styles/STE`):
  - sentences of at most 25 words.
  - passive voice, as a warning.
  - a banned-word list, each word with a replacement (`utilize`→`use`, `via`→`through`, `in order to`→`to`, and so on).

  Vale is a Go binary, so pin its version and SHA-256 in CI.
- [ ] Lint Markdown prose with the STE linter from the vendored `asd-ste100` skill (`ste-lint.py`): semicolons, sentence length and synonym rotation. Treat logs such as `CHANGELOG.md` and this file without synonym rotation.
  - The skill comes from `danyuchn/asd-ste100-skill`. Check its licence before vendoring it.
  - Also check that our own banned-word list doesn't copy the ASD dictionary, which ASD forbids.
- [ ] Add a `Makefile` with `install`, `test`, `lint`, `format`, `markdown`, `vale` and `ste` targets, so CI and people run the same commands.

### Tests

- [ ] Configure pytest the way they do: `addopts = "-ra"`, a 60-second per-test timeout (`pytest-timeout`, `timeout_method = "thread"`), and `pytest-mock` in the dev group.
- [ ] Add `tests/test_single_source.py`, which pins every copy that can't import its source to that source. Candidates here:
  - `MIN_VOLUME_24H` and `TOP` quoted in `README.md`.
  - the 14:00 UTC schedule in `deploy/modal_app.py`, the README and the 15:30 UTC site workflow.
  - `PAPER_LIMITS` quoted in the README.
  - the E002 numbers in `research/papers/` and on the site.

### CI and repository automation

- [ ] Add a CI workflow for "Lint and tests" that runs on pull requests and on pushes to `main`. Today only the site workflow exists, so nothing runs lint or tests in CI.
  - Use `permissions: contents: read`, and a concurrency group that cancels superseded runs.
  - Use `setup-uv` with caching keyed on `uv.lock`.
- [ ] Add a nightly reproduction workflow. It re-runs a rotating sample of committed analyses and fails or opens an issue when a dependency upgrade changes a number. Here: rebuild each experiment's dev-split numbers from committed inputs. Never touch a holdout, because `research/trials.jsonl` refuses a second look.
- [ ] Deploy on push, with a schedule only as a backstop. Run crons off the top of the hour, because GitHub runs scheduled jobs late or not at all when Actions is busy.

### Review and process

- [ ] Add `REVIEW.md`, which Claude Code's code review reads. It flags duplicated definitions and poor comments, using the two skills below.
- [ ] Add a `single-source-of-truth` skill.
  - Each constant, threshold or list has one definition, and everything else imports it.
  - Search before adding a literal.
  - A copy that must exist (workflow, docs) gets a test and a comment naming its source.
  - The skill also says where each kind of definition lives in this repo.
- [ ] Add a `commenting` skill.
  - Comment only what the code can't say: an outside constraint, a choice that looks wrong, or a value with a source.
  - Never restate the code.
  - Never number steps.
  - Write comments in STE.
- [ ] Add a PR template with a summary and a checklist. For an experiment, the checklist says the prereg was committed before outcome data, the ledger entry exists and `check-paper` passes.
- [ ] Keep `CHANGELOG.md` in Keep a Changelog form, with an `[Unreleased]` section. Single-source the package version, and add a `release` skill that bumps it, dates the changelog and tags the merged commit.

## Code quality and tooling (their open TODOs)

- [ ] Add a JSON, JSONL and YAML linter. This covers `research/literature/*.jsonl`, `research/trials.jsonl`, `sources.yaml` and the workflows.
- [ ] Add `bandit` for security linting.
- [ ] Add a shell linter (`shellcheck`) for workflow `run:` blocks and any scripts.
- [ ] Run Cloudflare's [security-audit-skill](https://github.com/cloudflare/security-audit-skill) on the repo. This matters most for `src/predmark/execution/`, the Kalshi signing code and the workflows that hold secrets.
- [ ] Add accessibility linting for the website (see Site).
- [ ] Lint for early returns, to discourage nesting.
- [ ] Find a way to lint or encourage comments inside function bodies, not only on signatures.
- [ ] Don't allow personal data or toxic text in code comments.
- [ ] Add IFTTT-Lint ("if this, then that"). Mark a block with `LINT.IfChange` and `LINT.ThenChange(<path>)`, and fail when a diff changes the block but not the file it names. Candidates here: the Modal cron and the site cron, and `PAPER_LIMITS` and the README.
- [ ] Type-check in lint and CI with mypy, pyright or ty, ratcheted in. Start strict on `src/predmark/execution/`, where a type error can mean a wrong order, then widen package by package.
- [ ] Fail lint on comments that start with a step number (`# 1. Do X`). Leave numbered lists in docstrings and Markdown alone.
- [ ] Make sure all code is reachable, and remove what isn't.
  - Run `vulture` with a whitelist for false positives, so new dead code fails. Likely candidates: `predmark.data.recorder` (no command uses it) and the `lit` sources.
  - Turn on ruff's unreachable-code rule (`PLW0101`) when our ruff version has it.
  - Use a skill for removals that need judgement.
- [ ] Make sure every parameter is used: add ruff `ARG` and fix what it finds. Exempt `tests/` or use `@pytest.mark.usefixtures` there. Also check that each CLI option and `--set` key reaches the code it names.
- [ ] Measure test coverage in CI with a ratcheting minimum.
  - Use branch coverage, with `fail_under` set just below today's total.
  - Require coverage of the lines each PR changes (`diff-cover --fail-under=90` against `origin/main`).
  - Keep `make test` fast and add `make coverage`.
  - Add a `test-new-paths` skill that walks a diff and checks that each new branch has a test asserting on its result.
- [ ] Run mutation testing with `mutmut` on the code where a silent change matters most: `execution/risk.py`, `execution/fees.py`, `execution/engine.py`, `research/metrics.py` and `research/ledger.py`. Run it by hand or on a schedule, not per PR. Treat a surviving mutant as a missing test, not a gate.
- [ ] Add property-based tests with Hypothesis. Invariants to start with:
  - the ledger's P&L doesn't depend on fill order.
  - `worst_case_loss` is never negative and never below the loss in any outcome.
  - fees round up.
  - the bootstrap doesn't depend on row order.
  - Kalshi request signing round-trips.
- [ ] Add fuzz testing, starting with the parsers of untrusted input: Kalshi and Polymarket payloads, feeds and arXiv XML.
- [ ] Add a decision regression check. Recompute every committed `results.json` decision from stored inputs, and fail on a difference unless the experiment's amendments explain it.
- [ ] Check links in CI with `lychee`, on the built site and the external links in docs and papers. Check external links weekly, not per PR, so a flaky host doesn't block merges.
- [ ] Check the site after each deploy. Fetch the home page and a strategy page from GitHub Pages, and open or update an issue on failure.
- [ ] Follow Semantic Versioning. Before each bump, decide whether the change is major, minor or patch. While the version is `0.x`, state what counts as breaking, for example the CLI, the journal schema or the strategy interface.
  - The release skill proposes the bump with a reason, updates the version, dates the changelog and tags.
  - Before each release it also prunes this file: items that landed or went stale.
- [ ] Encourage signed commits, and sign every version tag (SSH signing, `gpg.format ssh`).
  - Document `commit.gpgsign` and `tag.gpgsign` in the README.
  - Keep an allowed-signers file.
  - Later, require signed commits on `main`.
- [ ] Open question: can any data files or dataclasses move to Protocol Buffers?
- [ ] Pin GitHub Actions to commit SHAs, with the tag as a comment. Add Dependabot (`.github/dependabot.yml`) for `github-actions` and `uv`. The site workflow already warns that its actions target Node 20.
- [ ] Check dependencies for known vulnerabilities in CI with `pip-audit` against `uv.lock`.
- [ ] Set `timeout-minutes` on every CI job. `site.yml` has none today.
- [ ] Scan for secrets in CI with `gitleaks` or `trufflehog`, on pull requests and once across history. The repo is public, and agents commit and push.
- [ ] Add a pre-commit config that runs the fast parts of lint.
- [ ] Run CI on macOS as well as Linux.

## Testing and performance

- [ ] Add performance tests for memory, compute and wall time, starting with a strategy pass and a site build.
- [ ] Make the tests run faster.
- [ ] Make lint run faster. Consider hashing what has already been checked.
- [ ] Open question: would C++ or another compiled language speed up any hot paths?

## Research integrity

These items are adapted from their "Protocol and science" and "Pipeline" sections.

- [ ] Measure decision stability. Rerun an analysis under perturbations that shouldn't matter: bootstrap seed, sample seed, an equivalent fee rounding. Report how often the decision flips.
- [ ] Add positive controls with a known answer. Plant an edge in synthetic market data and check that the full pipeline (panel, backtest, analysis) recovers it. This complements the synthetic tests of analysis code alone.
- [ ] Add a null sanity check. Run each strategy and analysis on shuffled outcomes, or on a random-entry strategy. Its "edge" should vanish, apart from costs.
- [ ] Account for retries. Each variant of an idea is another chance at a false positive. Report how many experiments preceded a supported result, and count variants against their parent idea.
- [ ] Record every candidate before its outcome is known. `research/trials.jsonl` logs evaluations. Also log ideas that were dropped or crashed before evaluation, so reported success rates aren't biased towards runs that finished.
- [ ] Monitor drift on a schedule, for venue rules and behaviour that change under the same name: fee multipliers, rate limits, API fields. Alert when they move.
- [ ] Harden agent prompts against prompt injection. Fence untrusted text (papers, feeds, market rules) with clear delimiters, and add a test with a planted instruction that must not change the output.
- [ ] Write an errata process. When a bug is found to affect committed results, record which ones it affects and the fixing commit. Show a banner on affected reports and site pages, without rewriting the original files.
- [ ] Keep analyses from going stale. Each write-up states its commit and data snapshot, and a `make analyses` target re-runs them and shows which numbers moved.
- [ ] Spend caps and a kill switch for agent and Modal loops.
  - Set a budget per run and per day, and stop cleanly at it.
  - The engine already honours `KILL` for orders. Add the same for compute.
- [ ] Run several agent loops on one machine safely: a worktree per loop, and a machine-wide lock around heavy jobs.
- [ ] Collect telemetry from agent transcripts (steps, failed commands, re-read files and tokens), and mine it for better skills. Mine process failures, not results, to avoid Goodhart. Keep it opt-in and redacted, and commit only aggregates.
- [ ] Record the environment that produced each result: OS, hosted environment and agent product. Keep it coarse, with no hostnames or usernames.
- [ ] Set a size budget for the repository, and decide where large data lives long term.
- [ ] Open question: should past experiments be searchable, as a query tool or MCP server, so agents can find similar work? Decide what agents may see, because hints from earlier results can bias a new one.
- [ ] Optimise agent prompts with DSPy, against a held-out split. Guard against Goodhart: never tune towards favourable results.

## Website

- [ ] Use colour-blind-safe colours everywhere, defined in one place. Pin them with a build-time test that simulates protanopia, deuteranopia and tritanopia in light and dark mode. Today the site's two series pass the dataviz validator, but no test pins them.
- [ ] Check accessibility in CI with `axe-core` or `pa11y` over the built pages, and fail on serious issues.
- [ ] Add a sitemap and an RSS or Atom feed, for example of new runs and results.
- [ ] Add page metadata (description, OpenGraph, canonical URL) and a 404 page.
- [ ] Compress the site so it loads faster.
- [ ] Show changelog versions on the site.
- [ ] Order research sections latest first.
- [ ] Publish a JSON Schema for the run log (`*.runs.jsonl`), so outside tools can read results.
- [ ] Support other languages for the site's own text, defaulting to the browser's language.
- [ ] Branding: logo, favicon, one palette and typeface, and social preview images.

## Housekeeping

- [ ] Clean up the README for a fast start. Lead with the shortest path to a first backtest and paper run, and move details further down or into `docs/`.
  - State every dependency up front, with what each is for: Git, `gh`, Claude Code, Python 3.12, uv and Modal.
- [ ] Add `CONTRIBUTING.md`, `SECURITY.md` and `CITATION.cff`.
  - `SECURITY.md` says how to report a leaked key or a way around the trading safeguards.
  - `CITATION.cff` lets others cite the papers, with a DOI per release.
- [ ] Decide whether to track work in GitHub issues or Linear instead of this file.
- [ ] Decide whether the repo needs a `SOUL.md`.
- [ ] Use academic language and APA style in papers. Put the rule in the `write-paper` skill and align `check-paper` with it.
- [ ] State at the top of each paper why it was written and what question it answers.
- [ ] When doing a task, always show some indicator of progress.
- [ ] Add a Slack bot that posts when a run completes or an experiment lands.

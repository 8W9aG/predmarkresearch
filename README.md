# predmarkresearch

Automated research on prediction markets, building toward strategies this system can execute on
Kalshi. Work moves through one pipeline:

```
literature -> ideas -> preregistered experiment -> paper
                              |
                              v  only with holdout evidence
                       paper trading -> Kalshi demo -> live (owner's decision)
```

## Setup

```sh
uv sync
uv run pytest
```

No credentials are needed for research or paper trading. See `.env.example` for optional keys.

## Research

Agent workflows live in `.claude/skills/`. In Claude Code, run:

| Command | What it does |
| --- | --- |
| `/lit-review` | Harvest papers and blogs into an index, and go through them one by one |
| `/research-cycle` | Preregister and run one experiment, with a new dataset or model where useful |
| `/write-paper` | Write up completed experiments, with citations and numbers checked |

The agents call these tools, which are not part of the CLI:

```sh
uv run python -m predmark.lit harvest        # arXiv, OpenAlex, Semantic Scholar, blog feeds
uv run python -m predmark.lit next           # next unreviewed record
uv run python -m predmark.research trials    # every evaluation ever run
uv run python -m predmark.research check-paper research/papers/P001-.../paper.md
uv run python -m predmark.research check-integrity   # the research rules CI enforces
uv run python -m predmark.collect list       # daily data collectors
uv run python -m predmark.jev ping           # check TYPESAFE_API_KEY with one Jev call
```

The rules that keep results honest are in `CLAUDE.md`: preregistration, one look at each holdout, an
append-only trial ledger, event-clustered uncertainty, and costs in every profitability claim.

## Data collections

Some data can only be gathered going forward: a quote as it stood, or a forecast made before the
outcome was known. A collector in `predmark.collect` records one such dataset each day, and never
trades. Each writes to `collect/<name>/` in the state directory, with a `runs.jsonl` of every run.
To add one, write a module that defines `COLLECTOR` and list it in `MODULES` in
`src/predmark/collect/__init__.py`.

### Jev forecasts

`jev-forecasts` asks TypeSafe's Jev (`predmark.jev`, model `jev-1.14.0`) whether each market in the
Nothing Ever Happens universe will resolve YES. Jev sees the market's text but no prices. Each record
holds Jev's probability, the listing quote, and the bet it implies: YES at the ask or NO at the bid
when the edge after the taker fee is at least 5 cents. The bet is recorded, never placed. Each
market is forecast once, and each run is capped at $0.25 of Jev spend (about $0.01 in practice).
Jev's training cutoff is unknown, so this data is only trusted forward. It is the data for E003
(`research/experiments/E003-jev-forecast/preregistration.md`). Jev is used for nothing else.

### Kalshi listing snapshots

`kalshi-listing` records the listing quote, displayed sizes, 24-hour and lifetime volume, open
interest and open and close times of every open Kalshi market with 24-hour volume of at least
`MIN_VOLUME_24H`, once a day. Kalshi serves no historical order books or listings, so this is what
lets a backtest replay what the live paper run saw. Each record carries its `rank` by 24-hour volume
(the Nothing Ever Happens order) so a replay can apply the `TOP` cap. Files are
`collect/kalshi-listing/snapshots/<UTC date>.jsonl.gz`, one market per line. The listing is fetched a
few minutes after the strategy pass's own, so quotes can differ a little.

## Strategies

The `predmark` CLI lists, backtests, runs, and inspects strategies registered in
`predmark.strategies`. Each strategy module defines a `STRATEGY` with a `run` function (one pass,
on paper by default) and optionally a `backtest`. To add one, write the module and list it in `MODULES` in
`src/predmark/strategies/__init__.py`. The CLI and the Modal job pick it up from there.

```sh
uv run predmark list
uv run predmark backtest nothing-ever-happens --panel data/e002/raw.jsonl --set min_volume=0
uv run predmark backtest nothing-ever-happens --start 2026-09-01 --end 2026-10-01 --series 300
uv run predmark run nothing-ever-happens --set max_new=20   # one paper pass
uv run predmark run all                                    # every strategy
uv run predmark status all                                 # offline account state
uv run predmark run nothing-ever-happens --venue kalshi-demo --set max_new=5   # demo exchange
uv run predmark status all --venue kalshi-demo
```

Strategy options go in `--set key=value`. Each strategy keeps its own journal,
`<state-dir>/<name>.sqlite`, with `data/paper` as the default. With `--venue kalshi-demo`, orders go
to Kalshi's demo exchange instead, the journal is `<name>.kalshi-demo.sqlite`, and the default
`RiskLimits` apply, so a pass stops at the first limit and says why. A demo pass first reconciles
the journal with the exchange and places no orders if they disagree. It needs `KALSHI_KEY_ID` and
`KALSHI_PRIVATE_KEY_PATH` for a demo account, exported in the shell, and a demo subaccount for the
strategy (see Execution). Production is not an option. A file named `KILL` in the state
directory halts new orders for all of them. Backtests are exploratory: a claim about a strategy
needs a preregistered experiment.

### Nothing Ever Happens

This strategy buys NO at the bid on popular Kalshi markets and holds to settlement. Popular means
at least 10,000 contracts traded in the last 24 hours, from the live listing at run time. The
backtest applies the same threshold to trailing candle volume. Each pass considers at most the
1,000 busiest markets, so a pass takes about 2 minutes. The options are `min_volume`, `top`,
`max_new`, `count`, and `cash`.

It is a baseline, not an edge. Across all markets, E002 found it loses about 18 cents per contract,
almost all of it spread (`research/experiments/E002-nothing-ever-happens/report.md`).

### On Modal

`deploy/modal_app.py` (app `predmark-strategies`) runs every registered strategy once a day at
14:00 UTC, paper only. Journals live on the Modal Volume `predmark-paper`, so positions carry over.
One strategy failing does not stop the others. After the strategies, the same job runs every data
collector (below). The app holds no venue credentials. Its one secret is `typesafe-jev`, for Jev.

```sh
uv run modal deploy deploy/modal_app.py
uv run modal run deploy/modal_app.py::status
uv run modal run deploy/modal_app.py::run --name nothing-ever-happens   # one pass now
uv run modal run deploy/modal_app.py::collect --name jev-forecasts      # one collection now
uv run modal app logs predmark-strategies
```

The `deploy-modal` workflow deploys the app on each push to `main` that changes `deploy/`,
`src/predmark/`, or the dependencies, after lint and tests pass. Its Modal token is in the GitHub
environment `modal-deploy`, which only `main` can use. Setup steps are at the top of the workflow.

To stop new orders, put a file named `KILL` on the volume. To stop the schedule, run
`modal app stop predmark-strategies`.

Paper sizing uses `PAPER_LIMITS` in `strategies/base.py` and $1,000,000 of paper cash. These limits
skip the account-wide worst-case check, so a strategy can hold every market. The account owner
approved them for paper only on 2026-11-08. Live and demo limits are unchanged. Paper fills pay a
direct Kalshi account's taker fee, rounded up to $0.0001, which matched every demo fill on
2026-11-11. Paper runs before then paid at least 1 cent per order (see `CHANGELOG.md`).

## Website

A static site shows every registered strategy, its paper performance over time, and the research
behind it. Rebuild it from the repo:

```sh
uv run python -m predmark.site --from-url "$PREDMARK_RUN_LOGS_URL"   # pull run logs, then build
uv run python -m predmark.site --from-modal   # same, read straight from the volume (Modal token)
uv run python -m predmark.site                # build from local runs in data/paper
open site/index.html
```

The output in `site/` is plain HTML, CSS, and one small script, with no external requests, so any
static host can serve it. It is gitignored.

The `site` workflow uses `--from-url`. It reads the `run_logs` web endpoint of the Modal app, which
is public and read-only and serves only the `*.runs.jsonl` files the site publishes anyway. So
GitHub holds no Modal token. Set the repo variable `RUN_LOGS_URL` to the endpoint URL that
`modal deploy` prints. A strategy's `evidence` lists the experiments whose
`report.md` the site links to.

## Execution

`predmark.execution.Engine` is the only route to a venue.

| Venue | Status |
| --- | --- |
| Paper, on live or replayed books | Works |
| Kalshi demo | Works. `--venue kalshi-demo` in the runner. The lifecycle check passed on the demo exchange on 2026-11-11. One subaccount per journal (see below) |
| Kalshi production | Off. Needs `env="prod"`, `allow_live=True`, and `PREDMARK_ARM_LIVE` |
| Polymarket | Data and paper only. Its docs list the US as close-only on the API |

Protections: a write-ahead order journal, risk checks that count resting orders at worst-case loss,
price collars, stale-book rejection, a daily loss halt, a kill file (`touch data/KILL`), idempotent
fills, and reconciliation against the venue. An order whose send outcome is unknown is never retried:
the engine halts until reconciliation resolves it.

To test the order lifecycle on Kalshi's demo exchange, with demo credentials exported:

```sh
uv run python -m predmark.execution.demo_check   # or --ticker <demo market>
```

It trades one contract at a time through `Engine`: rest, cancel, fill, close, and a crash whose
send response is lost and which `reconcile()` resolves after a restart. It reconciles after each
step, stops at the first failure and cancels what it left open, and writes the results to
`data/demo-check/`. Its journal, `data/demo-check/journal.sqlite`, persists between runs.

Each demo journal trades in its own Kalshi subaccount, set in `DEMO_SUBACCOUNTS` in
`execution/kalshi.py`: 1 for the check, 2 for Nothing Ever Happens. The engine syncs every fill
and position its venue can see, so two journals sharing a subaccount would count each other's
trades. The primary subaccount (0) holds unallocated funds. A journal records the account it first
traded on and refuses to open against another. A new strategy needs a subaccount, created with
`POST /portfolio/subaccounts` and funded with `POST /portfolio/subaccounts/transfer`, before it
can run on demo.

Kalshi also funds each exchange index (shard) separately and refuses an order on an unfunded one.
A strategy skips markets on an index where its subaccount has no cash, and counts them as
`unfunded`. Subaccount 2 has cash on indexes 0 and 2, which held every popular demo market on
2026-11-11. Manage subaccounts and shard cash with:

```sh
uv run python -m predmark.execution.demo_accounts balances
uv run python -m predmark.execution.demo_accounts create
uv run python -m predmark.execution.demo_accounts transfer --to-sub 2 --to-shard 2 --dollars 10
```

A transfer is sent once and never retried. Kalshi refuses an idempotency key on transfers that
involve a subaccount, so check `balances` before sending one again.

Kalshi's order and position listings trail its matching engine, by 0.3 to 0.7 seconds on demo.
`reconcile()` re-reads after `KalshiVenue.listing_lag_seconds` before it acts on a disagreement.

Not built yet: websocket feeds and per-series tick and fee lookup. The order book recorder in
`predmark.data.recorder` is not wired to any command.

## Layout

```
src/predmark/cli.py      list, backtest, run, status
src/predmark/backtest/   panel download and strategy backtests
src/predmark/strategies/ registry, shared paper helpers, runner, strategies
src/predmark/lit/        literature sources and library, for the /lit-review agent
src/predmark/data/       Kalshi and Polymarket market data, book recorder
src/predmark/research/   metrics, trial ledger, paper checks
src/predmark/execution/  engine, risk, fees, paper and Kalshi venues
deploy/modal_app.py      daily paper run of every strategy on Modal
src/predmark/site/       static website builder
research/literature/     INDEX.md, library.jsonl, sources.yaml
research/experiments/    one directory per experiment
research/papers/         papers
research/trials.jsonl    append-only record of evaluations
data/                    downloaded data, not in git
```

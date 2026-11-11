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

## Strategies

The `predmark` CLI lists, backtests, runs, and inspects strategies registered in
`predmark.strategies`. Each strategy module defines a `STRATEGY` with a `run` function (one paper
pass) and optionally a `backtest`. To add one, write the module and list it in `MODULES` in
`src/predmark/strategies/__init__.py`. The CLI and the Modal job pick it up from there.

```sh
uv run predmark list
uv run predmark backtest nothing-ever-happens --panel data/e002/raw.jsonl --set min_volume=0
uv run predmark backtest nothing-ever-happens --start 2026-09-01 --end 2026-10-01 --series 300
uv run predmark run nothing-ever-happens --set max_new=20   # one paper pass
uv run predmark run all                                    # every strategy
uv run predmark status all                                 # offline account state
```

Strategy options go in `--set key=value`. Each strategy keeps its own journal,
`<state-dir>/<name>.sqlite`, with `data/paper` as the default. A file named `KILL` in the state
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

To stop new orders, put a file named `KILL` on the volume. To stop the schedule, run
`modal app stop predmark-strategies`.

Paper sizing uses `PAPER_LIMITS` in `strategies/base.py` and $1,000,000 of paper cash. These limits
skip the account-wide worst-case check, so a strategy can hold every market. The account owner
approved them for paper only on 2026-11-08. Live and demo limits are unchanged. A one-contract
order pays at least 1 cent in fees, because fees round up per order.

## Website

A static site shows every registered strategy, its paper performance over time, and the research
behind it. Rebuild it from the repo:

```sh
uv run python -m predmark.site --from-modal   # pull run logs from the Modal volume, then build
uv run python -m predmark.site                # build from local runs in data/paper
open site/index.html
```

The output in `site/` is plain HTML, CSS, and one small script, with no external requests, so any
static host can serve it. It is gitignored. A strategy's `evidence` lists the experiments whose
`report.md` the site links to.

## Execution

`predmark.execution.Engine` is the only route to a venue.

| Venue | Status |
| --- | --- |
| Paper, on live or replayed books | Works |
| Kalshi demo | Implemented against the V2 order API, tested with mocks, not yet run against the exchange |
| Kalshi production | Off. Needs `env="prod"`, `allow_live=True`, and `PREDMARK_ARM_LIVE` |
| Polymarket | Data and paper only. Its docs list the US as close-only on the API |

Protections: a write-ahead order journal, risk checks that count resting orders at worst-case loss,
price collars, stale-book rejection, a daily loss halt, a kill file (`touch data/KILL`), idempotent
fills, and reconciliation against the venue. An order whose send outcome is unknown is never retried:
the engine halts until reconciliation resolves it.

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

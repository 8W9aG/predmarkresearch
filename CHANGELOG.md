# Changelog

All notable changes to this project are recorded here, in [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) form.

## [Unreleased]

### Added

- `predmark.jev`, a client for TypeSafe's Jev through its REST API (`POST /v1/systemone`), not the Vercel AI Gateway, which documents only a TypeScript SDK call. It pins `jev-1.14.0`, retries only 429, 529 and connection failures (requests that were never evaluated), and has a spend cap per client and a JSONL cost log in `data/jev/`. `python -m predmark.jev` has `models`, `ping` and `spend` commands. `TYPESAFE_API_KEY` is in `.env.example`. Checked live on 2026-11-08: the server adds about 265 input tokens to each request, so the spend cap's upper bound adds a margin for it.
- `predmark.collect`, a registry and runner for daily data collections. Each collector writes under `collect/<name>/` in the state directory and logs each run to `runs.jsonl`. They run in the daily Modal job after the strategies, and one failing does not stop the others. `python -m predmark.collect list | run`.
- `jev-forecasts`, the first collector. It records Jev's probability that each market in the Nothing Ever Happens universe resolves YES, the listing quote, and the bet that implies at a 5-cent margin after fees. No bet is placed. This is the data for E003, preregistered on 2026-11-08. The Modal secret `typesafe-jev` holds the key.

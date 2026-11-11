"""Every registered strategy and data collector on Modal, once a day.

Each strategy makes one paper pass against live Kalshi books. Then each collector in
`predmark.collect` records its data under `/state/collect/<name>/`. Collectors never trade.

Paper only. No venue credentials are given to this app, and nothing in it can reach a venue's order
endpoints. Each strategy's journal lives on a Modal Volume, so positions carry over between runs.

The one credential is the TypeSafe key (Modal secret `typesafe-jev`), used only by the
`jev-forecasts` collector. It can spend money on TypeSafe but cannot trade.

    uv run modal deploy deploy/modal_app.py                        # start the daily schedule
    uv run modal run deploy/modal_app.py::status                   # every strategy's account
    uv run modal run deploy/modal_app.py::run --name nothing-ever-happens   # one pass now
    uv run modal volume put predmark-paper KILL /KILL              # halt new orders everywhere
    uv run modal run deploy/modal_app.py::collect --name jev-forecasts      # one collector now
"""

from __future__ import annotations

import json
from pathlib import Path

import modal

ROOT = Path(__file__).resolve().parent.parent
STATE = "/state"

app = modal.App("predmark-strategies")
volume = modal.Volume.from_name("predmark-paper", create_if_missing=True)
image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install_from_pyproject(str(ROOT / "pyproject.toml"))
    .add_local_python_source("predmark")
)
common = {"image": image, "volumes": {STATE: volume}}
jev_secret = modal.Secret.from_name("typesafe-jev")


@app.function(
    **common,
    secrets=[jev_secret],
    schedule=modal.Cron("0 14 * * *"),  # 14:00 UTC daily
    timeout=2 * 3600,  # passes are minutes long; this bounds a stuck run's cost
    retries=0,
    max_containers=1,
)
def daily() -> dict:
    from predmark.collect.runner import run_all as collect_all
    from predmark.strategies.runner import run_all

    log = lambda m: print(m, flush=True)  # noqa: E731
    try:
        result = run_all(STATE, log=log)
        result = {"strategies": result, "collectors": collect_all(STATE, log=log)}
    finally:
        volume.commit()
    print(json.dumps(result, indent=2, default=str))
    return result


@app.function(**common, timeout=2 * 3600, retries=0, max_containers=1)
def run(name: str) -> dict:
    from predmark.strategies.runner import run_one

    try:
        result = run_one(name, STATE, log=lambda m: print(m, flush=True))
    finally:
        volume.commit()
    print(json.dumps(result, indent=2, default=str))
    return result


@app.function(**common, timeout=600)
def status(name: str = "") -> dict:
    from predmark.strategies import names
    from predmark.strategies.runner import status as account

    out = {n: account(n, STATE) for n in ([name] if name else names())}
    print(json.dumps(out, indent=2, default=str))
    return out


@app.function(**common, secrets=[jev_secret], timeout=3600, retries=0, max_containers=1)
def collect(name: str) -> dict:
    from predmark.collect.runner import run_one

    try:
        result = run_one(name, STATE, log=lambda m: print(m, flush=True))
    finally:
        volume.commit()
    print(json.dumps(result, indent=2, default=str))
    return result

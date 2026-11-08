"""Every registered strategy on Modal: one paper pass each, once a day, against live Kalshi books.

Paper only. No credentials are given to this app, and nothing in it can reach a venue's order
endpoints. Each strategy's journal lives on a Modal Volume, so positions carry over between runs.

    uv run modal deploy deploy/modal_app.py                        # start the daily schedule
    uv run modal run deploy/modal_app.py::status                   # every strategy's account
    uv run modal run deploy/modal_app.py::run --name nothing-ever-happens   # one pass now
    uv run modal volume put predmark-paper KILL /KILL              # halt new orders everywhere
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


@app.function(
    **common,
    schedule=modal.Cron("0 14 * * *"),  # 14:00 UTC daily
    timeout=2 * 3600,  # passes are minutes long; this bounds a stuck run's cost
    retries=0,
    max_containers=1,
)
def daily() -> dict:
    from predmark.strategies.runner import run_all

    try:
        result = run_all(STATE, log=lambda m: print(m, flush=True))
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

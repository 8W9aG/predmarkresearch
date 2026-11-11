"""Daily data collections: datasets that can only be gathered going forward.

A collector records something at the time it is known, such as a quote, a listing or a model's
forecast, so later experiments can use it without look-ahead. Collectors never trade.

A collector is a module that defines `COLLECTOR` (a `predmark.collect.base.Collector`) and is listed
in MODULES. The runner gives each one its own directory, `<state>/collect/<name>/`, and appends a
summary of every run to `runs.jsonl` there. One failing collector does not stop the others.

    uv run python -m predmark.collect list
    uv run python -m predmark.collect run jev-forecasts --state-dir data/paper --set max_new=5
"""

from __future__ import annotations

from importlib import import_module

from .base import Collector

MODULES = ("jev_forecasts", "kalshi_listing")


def registry() -> dict[str, Collector]:
    collectors = [import_module(f"{__name__}.{m}").COLLECTOR for m in MODULES]
    return {c.name: c for c in collectors}


def names() -> list[str]:
    return list(registry())


def get(name: str) -> Collector:
    found = registry()
    if name not in found:
        raise KeyError(f"unknown collector {name!r}; known: {', '.join(found)}")
    return found[name]

"""Strategy registry. To add a strategy, write a module that defines `STRATEGY` and list it here."""

from __future__ import annotations

from importlib import import_module

from .base import Strategy

MODULES = ("nothing_ever_happens",)


def registry() -> dict[str, Strategy]:
    strategies = [import_module(f"{__name__}.{m}").STRATEGY for m in MODULES]
    return {s.name: s for s in strategies}


def names() -> list[str]:
    return list(registry())


def get(name: str) -> Strategy:
    found = registry()
    if name not in found:
        raise KeyError(f"unknown strategy {name!r}; known: {', '.join(found)}")
    return found[name]

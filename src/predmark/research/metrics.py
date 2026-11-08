"""Forecast evaluation with clustered uncertainty.

Contracts in one event are strongly dependent: a strike ladder resolves as one outcome. Treating
them as independent makes confidence intervals far too narrow, so every interval here resamples
whole clusters.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np


def brier(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def log_loss(p: np.ndarray, y: np.ndarray, eps: float = 1e-6) -> float:
    q = np.clip(p, eps, 1 - eps)
    return float(-np.mean(y * np.log(q) + (1 - y) * np.log(1 - q)))


def logit(p: np.ndarray, eps: float = 1e-6) -> np.ndarray:
    q = np.clip(p, eps, 1 - eps)
    return np.log(q / (1 - q))


def recalibration(p: np.ndarray, y: np.ndarray, iterations: int = 50) -> tuple[float, float]:
    """Fit logit P(y=1) = a + b * logit(p). Perfect calibration is a = 0, b = 1.

    b > 1 means prices are under-confident (too close to 0.5); b < 1 means over-confident.
    """
    X = np.column_stack([np.ones_like(p), logit(p)])
    beta = np.array([0.0, 1.0])
    for _ in range(iterations):
        eta = np.clip(X @ beta, -30, 30)
        mu = 1 / (1 + np.exp(-eta))
        w = np.clip(mu * (1 - mu), 1e-9, None)
        hessian = (X * w[:, None]).T @ X + 1e-9 * np.eye(2)
        step = np.linalg.solve(hessian, X.T @ (y - mu))
        beta = beta + step
        if np.max(np.abs(step)) < 1e-8:
            break
    return float(beta[0]), float(beta[1])


def calibration_table(p: np.ndarray, y: np.ndarray, bins: int = 10) -> list[dict[str, float]]:
    edges = np.linspace(0, 1, bins + 1)
    idx = np.minimum(np.digitize(p, edges[1:-1], right=False), bins - 1)
    rows = []
    for i in range(bins):
        mask = idx == i
        if mask.any():
            rows.append(
                {
                    "low": float(edges[i]),
                    "high": float(edges[i + 1]),
                    "n": int(mask.sum()),
                    "mean_price": float(p[mask].mean()),
                    "win_rate": float(y[mask].mean()),
                }
            )
    return rows


def ece(p: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    rows = calibration_table(p, y, bins)
    total = sum(r["n"] for r in rows)
    return float(sum(r["n"] / total * abs(r["mean_price"] - r["win_rate"]) for r in rows))


def cluster_bootstrap(
    statistic: Callable[[np.ndarray], float | tuple[float, ...]],
    clusters: np.ndarray,
    n_boot: int = 2000,
    seed: int = 0,
) -> np.ndarray:
    """Resample whole clusters. `statistic` takes row indices; returns n_boot rows of draws."""
    rng = np.random.default_rng(seed)
    labels, inverse = np.unique(clusters, return_inverse=True)
    groups = [np.flatnonzero(inverse == i) for i in range(len(labels))]
    draws = []
    for _ in range(n_boot):
        chosen = rng.integers(0, len(groups), len(groups))
        idx = np.concatenate([groups[i] for i in chosen])
        draws.append(np.atleast_1d(statistic(idx)))
    return np.array(draws)


def interval(draws: np.ndarray, level: float = 0.95) -> tuple[float, float]:
    alpha = (1 - level) / 2
    low, high = np.quantile(draws, [alpha, 1 - alpha])
    return float(low), float(high)

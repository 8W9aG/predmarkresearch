"""Rate-limited JSON client for idempotent reads. Order placement never goes through retries."""

from __future__ import annotations

import random
import threading
import time
from typing import Any

import httpx

RETRYABLE = {429, 500, 502, 503, 504}


class RateLimiter:
    def __init__(self, per_second: float):
        self.interval = 1.0 / per_second
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = self._next - now
            self._next = max(now, self._next) + self.interval
        if delay > 0:
            time.sleep(delay)


class ReadClient:
    def __init__(
        self,
        base_url: str,
        per_second: float = 5.0,
        max_retries: int = 5,
        backoff: float = 1.0,
        timeout: float = 30.0,
        headers: dict[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        self.max_retries = max_retries
        self.backoff = backoff
        self.limiter = RateLimiter(per_second)
        self.http = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "predmarkresearch/0.1", **(headers or {})},
            transport=transport,
        )

    def get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self.limiter.wait()
            try:
                resp = self.http.get(path, params=params)
                if resp.status_code not in RETRYABLE:
                    resp.raise_for_status()
                    return resp
                last = httpx.HTTPStatusError(
                    f"HTTP {resp.status_code} for {path}", request=resp.request, response=resp
                )
            except httpx.TransportError as exc:
                last = exc
            if attempt < self.max_retries:
                time.sleep(self.backoff * 2**attempt * (1 + random.random() / 2))
        assert last is not None
        raise last

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self.get(path, params).json()

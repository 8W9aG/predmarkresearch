"""Client for TypeSafe's Jev, a System One model that answers typed questions about text.

API: POST https://api.typesafe.ai/v1/systemone, per docs.typesafe.ai/api/system-one.md (2026-11-08).
Question types are `noul` (yes/no probability), `choice` and `score`. Jev cannot write free text.

Every call costs money, so the client has a spend cap per instance and appends each call to a cost
log. Jev's probabilities are not trusted as calibrated until we measure them (see TODO.md).
Text sent as `state` is often untrusted third-party data. Jev only scores it and cannot act on it.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from .http import RateLimiter

BASE_URL = "https://api.typesafe.ai"
# Pin a version, not `jev-latest`: an alias moves on release and changes answers silently.
DEFAULT_MODEL = "jev-1.14.0"
# docs.typesafe.ai/models.md, 2026-11-08: $0.042 per million input tokens, output free.
USD_PER_INPUT_TOKEN = Decimal("0.042") / 1_000_000
DEFAULT_CAP_USD = Decimal("0.50")
DEFAULT_LOG = Path("data/jev/cost.jsonl")

# 429 and 529 are rejections before evaluation (docs.typesafe.ai/api/errors.md), so a retry
# cannot double-bill. Timeouts and 5xx may have been evaluated and billed, so they are not retried.
RETRYABLE = {429, 529}


class SpendCapExceeded(RuntimeError):
    """The next request could take spend for this client past its cap."""


class JevError(RuntimeError):
    """Error status from the Jev API, with the status code and the server's detail."""

    def __init__(self, status: int, detail: Any):
        super().__init__(f"Jev HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


def noul(instructions: str, true: str | None = None, false: str | None = None) -> dict[str, Any]:
    """Build a yes/no question. `true` and `false` optionally say when each answer applies."""
    q: dict[str, Any] = {"type": "noul", "instructions": instructions}
    if true is not None or false is not None:
        q["criteria"] = {k: v for k, v in (("true", true), ("false", false)) if v is not None}
    return q


def choice(instructions: str, criteria: dict[str, str | None] | list[str]) -> dict[str, Any]:
    """`criteria` maps each choice name to when it applies. The API allows 1 to 255 choices."""
    if isinstance(criteria, list):
        criteria = {name: None for name in criteria}
    if not 1 <= len(criteria) <= 255:
        raise ValueError("choice needs 1 to 255 criteria")
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def score(instructions: str, levels: list[str]) -> dict[str, Any]:
    """`levels` are ordered from score 0 upwards. The API allows 1 to 10 levels."""
    if not 1 <= len(levels) <= 10:
        raise ValueError("score needs 1 to 10 levels")
    return {"type": "score", "instructions": instructions, "criteria": list(levels)}


@dataclass(frozen=True)
class Result:
    """Answers to one Jev request, with its token usage and cost."""

    model: str
    answers: dict[str, dict[str, Any]]
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal

    def prob(self, name: str) -> float:
        """Probability of yes for a noul question."""
        return float(self.answers[name]["noul"])

    def probabilities(self, name: str) -> dict[str, float]:
        """Probability per option for a choice or score question."""
        return {k: float(v) for k, v in self.answers[name]["probabilities"].items()}


def _payload_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()


# Measured 2026-11-08 on jev-1.14.0: billed tokens were the body's bytes plus about 265 per request
# and under 10 per extra question. These margins are about twice that.
OVERHEAD_TOKENS_PER_REQUEST = 512
OVERHEAD_TOKENS_PER_QUESTION = 64


def upper_bound_cost(body: bytes, n_questions: int) -> Decimal:
    """Return a cost no lower than the real one.

    A BPE token is at least one byte of UTF-8, and the server adds its own prompt tokens.
    """
    tokens = len(body) + OVERHEAD_TOKENS_PER_REQUEST + OVERHEAD_TOKENS_PER_QUESTION * n_questions
    return tokens * USD_PER_INPUT_TOKEN


class JevClient:
    """Jev client with a spend cap, retries only on rejections, and a JSONL cost log.

    The API key comes from `api_key` or the `TYPESAFE_API_KEY` environment variable.
    Each call appends a line to `log_path` unless it is None.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        cap_usd: Decimal | str | float = DEFAULT_CAP_USD,
        log_path: Path | str | None = DEFAULT_LOG,
        purpose: str = "",
        per_second: float = 5.0,
        max_retries: int = 4,
        backoff: float = 0.5,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ):
        key = api_key or os.environ.get("TYPESAFE_API_KEY")
        if not key:
            raise RuntimeError("Set TYPESAFE_API_KEY (see .env.example)")
        self.model = model
        self.cap_usd = Decimal(str(cap_usd))
        self.spent_usd = Decimal(0)
        self.log_path = Path(log_path) if log_path is not None else None
        self.purpose = purpose
        self.run_id = uuid.uuid4().hex[:12]
        self.max_retries = max_retries
        self.backoff = backoff
        self.limiter = RateLimiter(per_second)
        self.http = httpx.Client(
            base_url=BASE_URL,
            timeout=timeout,
            headers={"Authorization": f"Bearer {key}", "User-Agent": "predmarkresearch/0.1"},
            transport=transport,
        )

    def __enter__(self) -> JevClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.http.close()

    def models(self) -> list[dict[str, Any]]:
        """Return the models the API key can use. This call uses no tokens."""
        return self._send("GET", "/v1/models").json()["models"]

    def ask(self, state: str | dict | list, questions: dict[str, dict[str, Any]]) -> Result:
        """Ask every question about one `state` in one request."""
        if not questions:
            raise ValueError("no questions")
        payload = {"model": self.model, "state": state, "questions": questions}
        body = _payload_bytes(payload)
        bound = upper_bound_cost(body, len(questions))
        if self.spent_usd + bound > self.cap_usd:
            raise SpendCapExceeded(
                f"spent ${self.spent_usd} of ${self.cap_usd}; this request could cost ${bound}"
            )
        entry: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "run_id": self.run_id,
            "purpose": self.purpose,
            "model_requested": self.model,
            "questions": sorted(questions),
            "state_sha256": hashlib.sha256(_payload_bytes({"s": state})).hexdigest(),
        }
        try:
            resp = self._send("POST", "/v1/systemone", content=body)
        except JevError as exc:
            # A 4xx or 529 is a rejection before evaluation, so nothing was billed.
            if exc.status < 500 or exc.status in RETRYABLE:
                self._log({**entry, "status": f"http_{exc.status}", "cost_usd": "0"})
            else:
                self._charge_unknown(entry, f"http_{exc.status}", bound)
            raise
        except httpx.HTTPError as exc:
            self._charge_unknown(entry, type(exc).__name__, bound)
            raise
        data = resp.json()
        usage = data["usage"]
        cost = int(usage["input_tokens"]) * USD_PER_INPUT_TOKEN
        self.spent_usd += cost
        result = Result(
            model=data["model"],
            answers=data["answers"],
            input_tokens=int(usage["input_tokens"]),
            output_tokens=int(usage["output_tokens"]),
            cost_usd=cost,
        )
        self._log(
            {
                **entry,
                "status": "ok",
                "model": result.model,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "cost_usd": str(cost),
            }
        )
        return result

    def _charge_unknown(self, entry: dict[str, Any], status: str, bound: Decimal) -> None:
        """The request may have been billed. Count the upper bound against the cap."""
        self.spent_usd += bound
        self._log({**entry, "status": status, "cost_usd": str(bound), "cost_is_bound": True})

    def _send(self, method: str, path: str, content: bytes | None = None) -> httpx.Response:
        headers = {"Content-Type": "application/json"} if content is not None else None
        for attempt in range(self.max_retries + 1):
            self.limiter.wait()
            try:
                resp = self.http.request(method, path, content=content, headers=headers)
            except httpx.ConnectError:
                # The connection never opened, so the server never saw the request.
                if attempt == self.max_retries:
                    raise
                time.sleep(self._delay(attempt, None))
                continue
            if resp.status_code < 400:
                return resp
            if resp.status_code not in RETRYABLE or attempt == self.max_retries:
                raise JevError(resp.status_code, _detail(resp))
            time.sleep(self._delay(attempt, resp.headers.get("retry-after")))
        raise AssertionError("unreachable")

    def _delay(self, attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                pass
        return self.backoff * 2**attempt * (1 + random.random() / 2)

    def _log(self, entry: dict[str, Any]) -> None:
        if self.log_path is None:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")


def _detail(resp: httpx.Response) -> Any:
    try:
        return resp.json().get("detail")
    except ValueError:
        return resp.text[:500]


def spend_summary(log_path: Path | str = DEFAULT_LOG) -> dict[str, Any]:
    """Total calls, tokens and cost in a cost log, overall and per purpose."""
    path = Path(log_path)
    total: dict[str, Any] = {"calls": 0, "input_tokens": 0, "cost_usd": Decimal(0), "purposes": {}}
    if not path.exists():
        return total
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        cost = Decimal(e.get("cost_usd", "0"))
        p = total["purposes"].setdefault(e.get("purpose") or "-", {"calls": 0, "cost_usd": 0})
        p["calls"] += 1
        p["cost_usd"] = Decimal(p["cost_usd"]) + cost
        total["calls"] += 1
        total["input_tokens"] += int(e.get("input_tokens", 0))
        total["cost_usd"] += cost
    return total


def main(argv: list[str] | None = None) -> None:
    """Run the command-line tool: list models, ping the API, or summarise the cost log."""
    import argparse

    parser = argparse.ArgumentParser(prog="python -m predmark.jev")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("models", help="list models the key can use (free, no tokens)")
    ping = sub.add_parser("ping", help="one tiny noul request, to check the key end to end")
    ping.add_argument("--model", default=DEFAULT_MODEL)
    spend = sub.add_parser("spend", help="summarise the cost log")
    spend.add_argument("--log", default=str(DEFAULT_LOG))
    args = parser.parse_args(argv)

    if args.cmd == "spend":
        print(json.dumps(spend_summary(args.log), indent=2, default=str))
        return
    with JevClient(purpose=f"cli-{args.cmd}", cap_usd="0.01") as client:
        if args.cmd == "models":
            for m in client.models():
                print(m["name"], m.get("release_date", ""), m.get("description", ""))
            return
        client.model = args.model
        r = client.ask(
            "Kalshi is a US prediction market exchange.",
            {"about_markets": noul("Is this text about prediction markets?")},
        )
        print(f"model={r.model} p(yes)={r.prob('about_markets'):.3f} tokens={r.input_tokens}")


if __name__ == "__main__":
    main()

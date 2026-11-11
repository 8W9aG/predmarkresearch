import json
from decimal import Decimal

import httpx
import pytest

from predmark import jev


def ok_response(request: httpx.Request, tokens: int = 1000) -> httpx.Response:
    body = json.loads(request.content)
    answers = {name: {"type": "noul", "noul": 0.9} for name in body["questions"]}
    return httpx.Response(
        200,
        json={
            "model": "jev-1.14.0",
            "answers": answers,
            "usage": {"input_tokens": tokens, "output_tokens": 3},
        },
    )


def client(handler, tmp_path, **kw) -> jev.JevClient:
    kw.setdefault("cap_usd", "1")
    return jev.JevClient(
        api_key="test-key",
        log_path=tmp_path / "cost.jsonl",
        backoff=0,
        per_second=1000,
        transport=httpx.MockTransport(handler),
        **kw,
    )


def log_lines(tmp_path) -> list[dict]:
    return [json.loads(x) for x in (tmp_path / "cost.jsonl").read_text().splitlines()]


def test_question_builders():
    assert jev.noul("q?") == {"type": "noul", "instructions": "q?"}
    assert jev.noul("q?", true="yes")["criteria"] == {"true": "yes"}
    assert jev.choice("c?", ["a", "b"])["criteria"] == {"a": None, "b": None}
    assert jev.score("s?", ["low", "high"])["criteria"] == ["low", "high"]
    with pytest.raises(ValueError):
        jev.score("s?", [str(i) for i in range(11)])
    with pytest.raises(ValueError):
        jev.choice("c?", {})


def test_ask_sends_pinned_model_and_logs_cost(tmp_path):
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return ok_response(request, tokens=1_000_000)

    c = client(handler, tmp_path, purpose="test")
    r = c.ask("some text", {"x": jev.noul("Is it?")})
    assert seen["auth"] == "Bearer test-key"
    assert seen["body"]["model"] == jev.DEFAULT_MODEL
    assert r.prob("x") == 0.9
    assert r.cost_usd == Decimal("0.042")
    assert c.spent_usd == Decimal("0.042")
    (entry,) = log_lines(tmp_path)
    assert entry["status"] == "ok"
    assert entry["purpose"] == "test"
    assert entry["model"] == "jev-1.14.0"
    assert entry["questions"] == ["x"]
    assert Decimal(entry["cost_usd"]) == Decimal("0.042")
    assert "some text" not in json.dumps(entry)


def test_retries_429_and_529_honouring_retry_after(tmp_path, monkeypatch):
    sleeps = []
    monkeypatch.setattr(jev.time, "sleep", sleeps.append)
    statuses = iter([429, 529])

    def handler(request):
        status = next(statuses, None)
        if status:
            return httpx.Response(status, headers={"retry-after": "2"}, json={"detail": "slow"})
        return ok_response(request)

    r = client(handler, tmp_path).ask("t", {"x": jev.noul("q")})
    assert r.input_tokens == 1000
    # The rate limiter also sleeps, for under a millisecond.
    assert [s for s in sleeps if s >= 1] == [2.0, 2.0]


@pytest.mark.parametrize("status", [500, 503])
def test_server_error_is_not_retried_and_counts_against_cap(tmp_path, status):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(status, json={"detail": "boom"})

    c = client(handler, tmp_path)
    with pytest.raises(jev.JevError):
        c.ask("t", {"x": jev.noul("q")})
    assert len(calls) == 1
    assert c.spent_usd > 0
    (entry,) = log_lines(tmp_path)
    assert entry["cost_is_bound"] is True


def test_timeout_is_not_retried(tmp_path):
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ReadTimeout("slow", request=request)

    c = client(handler, tmp_path)
    with pytest.raises(httpx.ReadTimeout):
        c.ask("t", {"x": jev.noul("q")})
    assert len(calls) == 1
    assert c.spent_usd > 0


def test_connect_error_is_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(jev.time, "sleep", lambda s: None)
    attempts = iter([True])

    def handler(request):
        if next(attempts, False):
            raise httpx.ConnectError("refused", request=request)
        return ok_response(request)

    assert client(handler, tmp_path).ask("t", {"x": jev.noul("q")}).prob("x") == 0.9


def test_validation_error_is_not_billed(tmp_path):
    def handler(request):
        return httpx.Response(422, json={"detail": [{"msg": "bad"}]})

    c = client(handler, tmp_path)
    with pytest.raises(jev.JevError) as exc:
        c.ask("t", {"x": jev.noul("q")})
    assert exc.value.status == 422
    assert c.spent_usd == 0


def test_spend_cap_refuses_before_sending(tmp_path):
    calls = []

    def handler(request):
        calls.append(1)
        return ok_response(request, tokens=10_000_000)

    c = client(handler, tmp_path, cap_usd="0.5")
    c.ask("t", {"x": jev.noul("q")})
    assert c.spent_usd == Decimal("0.42")
    with pytest.raises(jev.SpendCapExceeded):
        c.ask("t" * 2_000_000, {"x": jev.noul("q")})
    assert len(calls) == 1


def test_upper_bound_covers_bytes_and_server_overhead():
    body = "é".encode()
    tokens = 2 + jev.OVERHEAD_TOKENS_PER_REQUEST + jev.OVERHEAD_TOKENS_PER_QUESTION
    assert jev.upper_bound_cost(body, 1) == tokens * jev.USD_PER_INPUT_TOKEN


@pytest.mark.parametrize(
    "body_bytes,n_questions,billed",
    # Real jev-1.14.0 calls on 2026-11-08.
    [(87, 1, 268), (126, 2, 276), (248, 5, 300), (1086, 1, 768), (129, 1, 290), (113, 1, 292)],
)
def test_upper_bound_exceeds_observed_billing(body_bytes, n_questions, billed):
    bound = jev.upper_bound_cost(b"x" * body_bytes, n_questions)
    assert bound >= billed * jev.USD_PER_INPUT_TOKEN


def test_missing_key(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="TYPESAFE_API_KEY"):
        jev.JevClient()


def test_spend_summary(tmp_path):
    c = client(lambda r: ok_response(r, tokens=1_000_000), tmp_path, purpose="lit")
    c.ask("a", {"x": jev.noul("q")})
    c.ask("b", {"x": jev.noul("q")})
    s = jev.spend_summary(tmp_path / "cost.jsonl")
    assert s["calls"] == 2
    assert s["cost_usd"] == Decimal("0.084")
    assert s["purposes"]["lit"]["calls"] == 2

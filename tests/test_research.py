import json

import numpy as np
import pytest

from predmark.http import ReadClient
from predmark.research.ledger import (
    HoldoutAlreadyUsed,
    claim_holdout,
    read_trials,
    record_trial,
)
from predmark.research.metrics import (
    brier,
    calibration_table,
    cluster_bootstrap,
    ece,
    interval,
    recalibration,
)
from predmark.research.papers import check_paper


def test_recalibration_recovers_known_slope():
    rng = np.random.default_rng(1)
    logits = rng.normal(0, 1.5, 200_000)
    y = (rng.random(logits.size) < 1 / (1 + np.exp(-(1.3 * logits)))).astype(float)
    a, b = recalibration(1 / (1 + np.exp(-logits)), y)
    assert abs(a) < 0.03 and abs(b - 1.3) < 0.03


def test_metrics_on_perfect_forecast():
    y = np.array([0.0, 1.0])
    assert brier(y, y) == 0 and ece(y, y) == 0
    assert sum(r["n"] for r in calibration_table(np.array([0.0, 0.5, 1.0]), np.zeros(3))) == 3


def test_cluster_bootstrap_is_wider_when_rows_are_duplicated_within_clusters():
    rng = np.random.default_rng(2)
    values = np.repeat(rng.normal(size=50), 20)
    clusters = np.repeat(np.arange(50), 20)
    clustered = interval(cluster_bootstrap(lambda i: values[i].mean(), clusters, 500))
    naive = interval(cluster_bootstrap(lambda i: values[i].mean(), np.arange(1000), 500))
    assert clustered[1] - clustered[0] > 2 * (naive[1] - naive[0])


def test_holdout_can_be_claimed_once(tmp_path):
    ledger, prereg = tmp_path / "t.jsonl", tmp_path / "p.md"
    prereg.write_text("plan")
    claim_holdout("E1", ledger)
    record_trial("E1", "dev", prereg, "h", {}, ledger)
    claim_holdout("E1", ledger)  # a dev run does not spend the holdout
    record_trial("E1", "holdout", prereg, "h", {"x": 1}, ledger)
    with pytest.raises(HoldoutAlreadyUsed):
        claim_holdout("E1", ledger)
    assert len(read_trials(ledger)) == 2


def test_check_paper_flags_unread_and_missing_citations(tmp_path):
    lib_path = tmp_path / "l.jsonl"
    lib_path.write_text(json.dumps({"key": "lovelace2025-aaaa", "status": "new"}) + "\n")
    exp = tmp_path / "exp" / "E001-x"
    exp.mkdir(parents=True)
    (exp / "results.json").write_text(json.dumps({}))
    paper = tmp_path / "p.md"
    paper.write_text("See [@lovelace2025-aaaa] and [@ghost2020-aaaa]; E001 and E999.")
    problems = check_paper(paper, lib_path, tmp_path / "exp")
    assert len(problems) == 3
    lib_path.write_text(json.dumps({"key": "lovelace2025-aaaa", "status": "abstract_read"}) + "\n")
    assert len(check_paper(paper, lib_path, tmp_path / "exp")) == 2


def test_read_client_retries_then_succeeds():
    import httpx

    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(429) if len(calls) < 3 else httpx.Response(200, json={"ok": True})

    client = ReadClient(
        "https://x.test", per_second=1000, backoff=0, transport=httpx.MockTransport(handler)
    )
    assert client.get_json("/") == {"ok": True}
    assert len(calls) == 3


def test_settled_markets_never_send_mve_filter_to_the_archive():
    import httpx

    from predmark.data.kalshi import KalshiData

    seen = []

    def handler(request):
        seen.append(request.url)
        if "historical" in request.url.path:
            assert "mve_filter" not in request.url.params
            rows = [{"ticker": "A"}, {"ticker": "B"}, {"ticker": "C", "mve_collection_ticker": "X"}]
        else:
            rows = [{"ticker": "A"}]
        return httpx.Response(200, json={"markets": rows, "cursor": ""})

    client = ReadClient("https://x.test", per_second=1000, transport=httpx.MockTransport(handler))
    tickers = [m["ticker"] for m in KalshiData(client=client).settled_markets("S")]
    assert tickers == ["A", "B"]
    assert len(seen) == 2


def _git_repo(tmp_path):
    import subprocess

    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")

    def commit(files, msg="c"):
        for name, text in files.items():
            p = tmp_path / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        git("add", "-A")
        git("commit", "-q", "-m", msg)

    return commit


def _trial(exp, prereg_text, split="dev"):
    import hashlib

    sha = hashlib.sha256(prereg_text.encode()).hexdigest()
    return json.dumps({"experiment": exp, "split": split, "prereg_sha256": sha}) + "\n"


EXP = "research/experiments/E009-x/"


def test_integrity_passes_when_prereg_precedes_results(tmp_path):
    from predmark.research.integrity import check_integrity

    commit = _git_repo(tmp_path)
    commit({EXP + "preregistration.md": "plan"})
    commit({EXP + "results.json": "{}", "research/trials.jsonl": _trial("E009", "plan")})
    commit({"research/trials.jsonl": _trial("E009", "plan") + _trial("E009", "plan", "holdout")})
    assert check_integrity(tmp_path) == []


def test_integrity_flags_an_edited_ledger_line(tmp_path):
    from predmark.research.integrity import check_ledger_append_only

    commit = _git_repo(tmp_path)
    commit({"research/trials.jsonl": '{"a": 1}\n{"b": 2}\n'})
    commit({"research/trials.jsonl": '{"a": 1}\n{"b": 3}\n{"c": 4}\n'})
    commit({"research/trials.jsonl": '{"a": 1}\n{"b": 3}\n{"c": 4}\n{"d": 5}\n'})
    problems = check_ledger_append_only(tmp_path)
    assert len(problems) == 1 and "removes or changes" in problems[0]


def test_integrity_flags_a_deleted_ledger(tmp_path):
    import subprocess

    from predmark.research.integrity import check_ledger_append_only

    commit = _git_repo(tmp_path)
    commit({"research/trials.jsonl": '{"a": 1}\n'})
    subprocess.run(["git", "-C", str(tmp_path), "rm", "-q", "research/trials.jsonl"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "rm"], check=True)
    assert len(check_ledger_append_only(tmp_path)) == 1


def test_integrity_flags_results_in_the_same_commit_as_prereg(tmp_path):
    from predmark.research.integrity import check_prereg_order

    commit = _git_repo(tmp_path)
    commit({EXP + "preregistration.md": "plan", EXP + "results.json": "{}"})
    problems = check_prereg_order(tmp_path)
    assert len(problems) == 1 and "not committed after" in problems[0]


def test_integrity_flags_results_committed_before_prereg(tmp_path):
    from predmark.research.integrity import check_prereg_order

    commit = _git_repo(tmp_path)
    commit({EXP + "results.json": "{}"})
    commit({EXP + "preregistration.md": "plan"})
    assert len(check_prereg_order(tmp_path)) == 1


def test_integrity_flags_a_prereg_edited_after_evaluation(tmp_path):
    from predmark.research.integrity import check_prereg_order

    commit = _git_repo(tmp_path)
    commit({EXP + "preregistration.md": "plan"})
    commit({EXP + "results.json": "{}", "research/trials.jsonl": _trial("E009", "plan")})
    commit({EXP + "preregistration.md": "a better plan"})
    problems = check_prereg_order(tmp_path)
    assert len(problems) == 1 and "amendments.md" in problems[0]


def test_integrity_flags_a_failing_paper(tmp_path):
    from predmark.research.integrity import check_integrity

    commit = _git_repo(tmp_path)
    commit(
        {
            "research/literature/library.jsonl": "",
            "research/papers/P009-x/paper.md": "As E042 shows [@nobody].",
        }
    )
    problems = check_integrity(tmp_path)
    assert len(problems) == 2 and all(p.startswith("research/papers/P009-x") for p in problems)

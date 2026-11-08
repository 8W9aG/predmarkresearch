import json

import numpy as np
import pytest
from defusedxml.common import EntitiesForbidden

from predmark.http import ReadClient
from predmark.lit.library import Library
from predmark.lit.sources import Record, parse_arxiv, parse_feed, parse_openalex
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

ARXIV = """<feed xmlns="http://www.w3.org/2005/Atom"><entry>
<id>http://arxiv.org/abs/2501.00001v2</id><published>2025-01-02T00:00:00Z</published>
<title>A  Paper</title><summary> About markets </summary>
<author><name>Ada Lovelace</name></author></entry></feed>"""


def rec(title="A Paper", **kw):
    return Record(
        title=title,
        authors=["Ada Lovelace"],
        date="2025-01-02",
        url="u",
        abstract="a",
        source=kw.pop("source", "x"),
        **kw,
    )


def test_parse_arxiv():
    r = parse_arxiv(ARXIV)[0]
    assert (r.arxiv_id, r.title, r.abstract) == ("2501.00001", "A Paper", "About markets")


def test_parse_arxiv_rate_limit_text_is_an_error():
    with pytest.raises(ValueError):
        parse_arxiv("Rate exceeded.")


def test_parse_openalex_rebuilds_abstract():
    payload = {
        "results": [
            {
                "title": "T",
                "doi": "https://doi.org/10.1/x",
                "authorships": [],
                "abstract_inverted_index": {"hello": [0], "world": [1]},
            }
        ]
    }
    r = parse_openalex(payload)[0]
    assert r.abstract == "hello world" and r.doi == "10.1/x"


def test_parse_feed_rss_and_atom():
    rss = "<rss><channel><item><title>P</title><link>http://a</link></item></channel></rss>"
    assert parse_feed(rss, "B")[0].url == "http://a"
    atom = (
        '<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Q</title>'
        '<link href="http://b"/></entry></feed>'
    )
    assert parse_feed(atom, "B")[0].url == "http://b"


def test_feed_rejects_entity_expansion():
    evil = (
        '<!DOCTYPE x [<!ENTITY a "b">]>'
        "<rss><channel><item><title>&a;</title></item></channel></rss>"
    )
    with pytest.raises(EntitiesForbidden):
        parse_feed(evil, "B")


def test_library_dedupes_across_sources_and_keeps_review(tmp_path):
    path = tmp_path / "l.jsonl"
    lib = Library(path)
    assert lib.add(rec(arxiv_id="1", source="arxiv")) is True
    key = next(iter(lib.records))
    lib.review(key, status="abstract_read", note="useful")
    assert lib.add(rec(title="A paper!", doi="10.1/x", source="openalex")) is False
    lib.save()
    again = Library(path).records[key]
    assert again["status"] == "abstract_read" and again["note"] == "useful"
    assert again["doi"] == "10.1/x" and again["sources"] == ["arxiv", "openalex"]


def test_library_rejects_unknown_status(tmp_path):
    lib = Library(tmp_path / "l.jsonl")
    lib.add(rec())
    with pytest.raises(ValueError):
        lib.review(next(iter(lib.records)), status="great")


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
    lib = Library(lib_path)
    lib.add(rec())
    key = next(iter(lib.records))
    lib.save()
    exp = tmp_path / "exp" / "E001-x"
    exp.mkdir(parents=True)
    (exp / "results.json").write_text(json.dumps({}))
    paper = tmp_path / "p.md"
    paper.write_text(f"See [@{key}] and [@ghost2020-aaaa]; E001 and E999.")
    problems = check_paper(paper, lib_path, tmp_path / "exp")
    assert len(problems) == 3
    lib = Library(lib_path)
    lib.review(key, status="abstract_read")
    lib.save()
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


def test_index_lists_every_record_and_next_skips_reviewed(tmp_path):
    lib = Library(tmp_path / "l.jsonl")
    lib.add(Record("Low | cited", ["A B"], "2025-01-01", "http://a", None, "x", citations=1))
    lib.add(Record("High cited", ["C D"], "2024-01-01", "http://b", None, "x", citations=9))
    lib.add(Record("A blog", ["E F"], "2026-01-01", "http://c", None, "x", kind="blog"))
    first = lib.next_new()
    assert first["title"] == "High cited"
    lib.review(first["key"], status="rejected")
    assert lib.next_new()["title"] == "Low | cited"
    path = tmp_path / "INDEX.md"
    lib.write_index(path)
    text = path.read_text()
    assert "1 of 3 reviewed." in text
    assert "Low \\| cited" in text
    assert text.index("High cited") < text.index("Low") < text.index("A blog")


def test_on_topic_records_outrank_highly_cited_off_topic_ones(tmp_path):
    lib = Library(tmp_path / "l.jsonl")
    lib.add(Record("Stone Age sites", ["A B"], "2024-01-01", "http://a", None, "x", citations=9999))
    lib.add(
        Record("Pricing", ["C D"], "2024-01-01", "http://b", "On Kalshi data", "x", citations=1)
    )
    lib.add(Record("Polymarket post", ["E F"], "2024-01-01", "http://c", None, "x", kind="blog"))
    assert [r["title"] for r in lib.ordered()] == ["Pricing", "Polymarket post", "Stone Age sites"]

"""Tests for scripts/lit.py, the literature tools behind /lit-review."""

import pytest
from defusedxml.common import EntitiesForbidden
from lit import Library, Record, parse_arxiv, parse_feed, parse_openalex

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


def test_semantic_scholar_429_without_key_names_the_fix(monkeypatch):
    import httpx
    import lit

    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)
    request = httpx.Request("GET", "https://api.semanticscholar.org/graph/v1/paper/search")
    response = httpx.Response(429, request=request)

    def rate_limited(self, path, params=None):
        raise httpx.HTTPStatusError("HTTP 429", request=request, response=response)

    monkeypatch.setattr(lit.ReadClient, "get_json", rate_limited)
    with pytest.raises(RuntimeError, match="SEMANTIC_SCHOLAR_API_KEY"):
        lit.fetch_semantic_scholar("q", 5, 2024)

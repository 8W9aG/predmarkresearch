"""Fetch literature records from arXiv, OpenAlex, Semantic Scholar, and blog feeds.

Everything fetched here is untrusted third-party text. It is stored as data, never as instructions.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any
from xml.etree.ElementTree import Element

import httpx
from defusedxml import ElementTree

from ..http import ReadClient

ATOM = "{http://www.w3.org/2005/Atom}"


@dataclass
class Record:
    title: str
    authors: list[str]
    date: str | None
    url: str | None
    abstract: str | None
    source: str
    kind: str = "paper"
    doi: str | None = None
    arxiv_id: str | None = None
    venue: str | None = None
    citations: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _clean(text: str | None) -> str | None:
    if not text:
        return None
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip() or None


def parse_arxiv(xml_text: str) -> list[Record]:
    if not xml_text.lstrip().startswith("<"):
        raise ValueError(f"arXiv returned a non-XML response: {xml_text[:80]!r}")
    root = ElementTree.fromstring(xml_text)
    out = []
    for e in root.findall(f"{ATOM}entry"):
        url = (e.findtext(f"{ATOM}id") or "").strip()
        arxiv_id = re.sub(r"v\d+$", "", url.rsplit("/abs/", 1)[-1]) if "/abs/" in url else None
        out.append(
            Record(
                title=_clean(e.findtext(f"{ATOM}title")) or "",
                authors=[
                    _clean(a.findtext(f"{ATOM}name")) or "" for a in e.findall(f"{ATOM}author")
                ],
                date=(e.findtext(f"{ATOM}published") or "")[:10] or None,
                url=url,
                abstract=_clean(e.findtext(f"{ATOM}summary")),
                source="arxiv",
                arxiv_id=arxiv_id,
                venue="arXiv",
            )
        )
    return out


def fetch_arxiv(query: str, limit: int, client: ReadClient | None = None) -> list[Record]:
    client = client or ReadClient(
        "https://export.arxiv.org", per_second=0.3, max_retries=2, backoff=5
    )
    resp = client.get(
        "/api/query",
        {
            "search_query": query,
            "max_results": limit,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        },
    )
    return parse_arxiv(resp.text)


def _openalex_abstract(index: dict[str, list[int]] | None) -> str | None:
    if not index:
        return None
    words = sorted((pos, word) for word, positions in index.items() for pos in positions)
    return " ".join(word for _, word in words)


def parse_openalex(payload: dict[str, Any]) -> list[Record]:
    out = []
    for w in payload.get("results") or []:
        ids = w.get("ids") or {}
        doi = (w.get("doi") or ids.get("doi") or "").removeprefix("https://doi.org/") or None
        location = w.get("primary_location") or {}
        out.append(
            Record(
                title=_clean(w.get("title")) or "",
                authors=[
                    (a.get("author") or {}).get("display_name") or ""
                    for a in w.get("authorships") or []
                ],
                date=w.get("publication_date"),
                url=location.get("landing_page_url") or w.get("id"),
                abstract=_openalex_abstract(w.get("abstract_inverted_index")),
                source="openalex",
                doi=doi,
                venue=(location.get("source") or {}).get("display_name"),
                citations=w.get("cited_by_count"),
            )
        )
    return out


def fetch_openalex(query: str, limit: int, since: str, client: ReadClient | None = None):
    client = client or ReadClient("https://api.openalex.org", per_second=2)
    params: dict[str, Any] = {
        "search": query,
        "per-page": min(limit, 200),
        "filter": f"from_publication_date:{since}",
        "sort": "cited_by_count:desc",
    }
    if key := os.environ.get("OPENALEX_API_KEY"):
        params["api_key"] = key
    return parse_openalex(client.get_json("/works", params))


def fetch_semantic_scholar(query: str, limit: int, since_year: int) -> list[Record]:
    key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
    client = ReadClient(
        "https://api.semanticscholar.org",
        per_second=0.5,
        max_retries=2,
        backoff=5,
        headers={"x-api-key": key} if key else None,
    )
    data = client.get_json(
        "/graph/v1/paper/search",
        {
            "query": query,
            "limit": min(limit, 100),
            "year": f"{since_year}-",
            "fields": (
                "title,authors,year,publicationDate,abstract,url,externalIds,venue,citationCount"
            ),
        },
    )
    out = []
    for p in data.get("data") or []:
        ext = p.get("externalIds") or {}
        out.append(
            Record(
                title=_clean(p.get("title")) or "",
                authors=[a.get("name") or "" for a in p.get("authors") or []],
                date=p.get("publicationDate") or (str(p["year"]) if p.get("year") else None),
                url=p.get("url"),
                abstract=_clean(p.get("abstract")),
                source="semantic_scholar",
                doi=ext.get("DOI"),
                arxiv_id=ext.get("ArXiv"),
                venue=p.get("venue") or None,
                citations=p.get("citationCount"),
            )
        )
    return out


def _local(el: Element) -> str:
    return el.tag.rsplit("}", 1)[-1]


def parse_feed(xml_text: str, name: str) -> list[Record]:
    root = ElementTree.fromstring(xml_text)
    out = []
    for item in root.iter():
        if _local(item) not in {"item", "entry"}:
            continue
        fields: dict[str, str] = {}
        for child in item:
            tag = _local(child)
            if tag == "link" and child.get("href"):
                fields.setdefault("link", child.get("href") or "")
            elif child.text:
                fields.setdefault(tag, child.text)
        date = fields.get("published") or fields.get("pubDate") or fields.get("updated")
        out.append(
            Record(
                title=_clean(fields.get("title")) or "",
                authors=[_clean(fields.get("creator") or fields.get("author")) or name],
                date=date,
                url=fields.get("link"),
                abstract=_clean(fields.get("summary") or fields.get("description")),
                source=f"feed:{name}",
                kind="blog",
                venue=name,
            )
        )
    return out


def fetch_feed(url: str, name: str) -> list[Record]:
    resp = httpx.get(
        url, timeout=30, follow_redirects=True, headers={"User-Agent": "predmarkresearch/0.1"}
    )
    resp.raise_for_status()
    return parse_feed(resp.text, name)

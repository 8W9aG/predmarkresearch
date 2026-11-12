"""Literature tools for the /lit-review skill: `uv run python scripts/lit.py <command>`.

Commands: harvest, list, index, next, show, review. The library is a git-tracked JSONL file
(`research/literature/library.jsonl`), deduplicated across sources, and `INDEX.md` is generated
from it. Everything fetched is untrusted third-party text. It is stored as data, never as
instructions. One failing source never stops a harvest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import Element

import httpx
import yaml
from defusedxml import ElementTree

from predmark.http import ReadClient

ATOM = "{http://www.w3.org/2005/Atom}"


@dataclass
class Record:
    """A literature record as one source reports it."""

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
    """Parse an arXiv Atom API response into records.

    Raise ValueError if the response is not XML.
    """
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
    """Fetch the newest arXiv papers that match `query`.

    The default client sends at most 0.3 requests per second.
    """
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
    """Parse an OpenAlex `/works` response into records."""
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
    """Fetch the most cited OpenAlex works for `query`, published on or after `since`.

    `since` is an ISO date. The request sends `OPENALEX_API_KEY` when it is set.
    """
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
    """Search Semantic Scholar for papers that match `query`, from `since_year` on.

    The request sends `SEMANTIC_SCHOLAR_API_KEY` when it is set. Without a key, a 429 response
    raises RuntimeError with instructions to set one.
    """
    key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
    # Without a key, requests share one public pool that answered about 1 in 12 on 2026-11-11.
    client = ReadClient(
        "https://api.semanticscholar.org",
        per_second=0.5,
        max_retries=2 if key else 5,
        backoff=5,
        headers={"x-api-key": key} if key else None,
    )
    params = {
        "query": query,
        "limit": min(limit, 100),
        "year": f"{since_year}-",
        "fields": "title,authors,year,publicationDate,abstract,url,externalIds,venue,citationCount",
    }
    try:
        data = client.get_json("/graph/v1/paper/search", params)
    except httpx.HTTPStatusError as exc:
        if key or exc.response.status_code != 429:
            raise
        raise RuntimeError(
            "rate-limited on the shared public pool. Set SEMANTIC_SCHOLAR_API_KEY in .env "
            "and run with `uv run --env-file .env`"
        ) from exc
    return _parse_semantic_scholar(data)


def _parse_semantic_scholar(data: dict[str, Any]) -> list[Record]:
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
    """Parse the items of an RSS or Atom feed into blog records.

    `name` is the venue, and the author when an item names none.
    """
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
        when = fields.get("published") or fields.get("pubDate") or fields.get("updated")
        out.append(
            Record(
                title=_clean(fields.get("title")) or "",
                authors=[_clean(fields.get("creator") or fields.get("author")) or name],
                date=when,
                url=fields.get("link"),
                abstract=_clean(fields.get("summary") or fields.get("description")),
                source=f"feed:{name}",
                kind="blog",
                venue=name,
            )
        )
    return out


def fetch_feed(url: str, name: str) -> list[Record]:
    """Fetch the RSS or Atom feed at `url` and parse it into blog records."""
    resp = httpx.get(
        url, timeout=30, follow_redirects=True, headers={"User-Agent": "predmarkresearch/0.1"}
    )
    resp.raise_for_status()
    return parse_feed(resp.text, name)


DEFAULT_PATH = Path("research/literature/library.jsonl")
INDEX_PATH = Path("research/literature/INDEX.md")
TOPIC = re.compile(
    r"prediction[- ]markets?|polymarket|kalshi|event contracts?|information markets?"
    r"|longshot|long-shot",
    re.IGNORECASE,
)
STATUSES = {"new", "relevant", "rejected", "abstract_read", "full_text_read"}
REVIEW_FIELDS = ("status", "relevance", "tags", "note")


def norm_title(title: str) -> str:
    """Normalize a title for matching: lowercase letters and digits, single spaces."""
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def citation_key(rec: dict[str, Any]) -> str:
    """Build a citation key from the first author's surname, the year, and a title hash.

    An example is `smith2024-1a2b`. The year is `nd` when the record has no date.
    """
    author = (rec.get("authors") or ["anon"])[0] or "anon"
    surname = re.sub(r"[^a-z]", "", author.split()[-1].lower()) if author.split() else "anon"
    year = (rec.get("date") or "")[:4]
    if not year.isdigit():
        match = re.search(r"\b(19|20)\d{2}\b", rec.get("date") or "")
        year = match.group(0) if match else "nd"
    digest = hashlib.sha1(norm_title(rec.get("title") or "").encode()).hexdigest()[:4]
    return f"{surname or 'anon'}{year}-{digest}"


class Library:
    """The literature library, read from and written to a JSONL file."""

    def __init__(self, path: str | Path = DEFAULT_PATH):
        self.path = Path(path)
        self.records: dict[str, dict[str, Any]] = {}
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                if line.strip():
                    rec = json.loads(line)
                    self.records[rec["key"]] = rec

    def _find(self, rec: dict[str, Any]) -> dict[str, Any] | None:
        title = norm_title(rec.get("title") or "")
        for other in self.records.values():
            if rec.get("doi") and (rec["doi"] or "").lower() == (other.get("doi") or "").lower():
                return other
            if rec.get("arxiv_id") and rec["arxiv_id"] == other.get("arxiv_id"):
                return other
            if title and title == norm_title(other.get("title") or ""):
                return other
        return None

    def add(self, record: Record) -> bool:
        """Merge a record. Returns True if new. Review fields are never overwritten by a harvest."""
        data = asdict(record)
        data.pop("extra", None)
        if not data["title"]:
            return False
        source = data.pop("source")
        existing = self._find(data)
        if existing:
            for key, value in data.items():
                if value and not existing.get(key):
                    existing[key] = value
            if data.get("citations") is not None:
                existing["citations"] = data["citations"]
            if source not in existing["sources"]:
                existing["sources"] = sorted([*existing["sources"], source])
            return False
        data.update(sources=[source], status="new", relevance=None, tags=[], note=None)
        data["key"] = citation_key(data)
        self.records[data["key"]] = data
        return True

    def review(self, key: str, **fields: Any) -> dict[str, Any]:
        """Set review fields on the record `key`, and return the record.

        Raise ValueError for a field outside REVIEW_FIELDS or an unknown status. Changes stay in
        memory until `save`.
        """
        rec = self.records[key]
        for name, value in fields.items():
            if name not in REVIEW_FIELDS:
                raise ValueError(f"not a review field: {name}")
            if name == "status" and value not in STATUSES:
                raise ValueError(f"unknown status {value}; expected one of {sorted(STATUSES)}")
            rec[name] = value
        return rec

    def save(self) -> None:
        """Write every record to the JSONL file, sorted by key."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(self.records[k], sort_keys=True) for k in sorted(self.records)]
        self.path.write_text("\n".join(lines) + ("\n" if lines else ""))

    def ordered(self) -> list[dict[str, Any]]:
        """Review order: on-topic before off-topic, papers before blogs, most cited, then newest."""
        by_date = sorted(self.records.values(), key=lambda r: r.get("date") or "", reverse=True)
        return sorted(
            by_date,
            key=lambda r: (
                not on_topic(r),
                r.get("kind") != "paper",
                -(r.get("citations") or 0),
            ),
        )

    def next_new(self) -> dict[str, Any] | None:
        """Return the first unreviewed record in review order, or None."""
        return next((r for r in self.ordered() if r["status"] == "new"), None)

    def write_index(self, path: str | Path = INDEX_PATH) -> None:
        """Write a generated table of every record. Titles are untrusted, so they are escaped."""
        rows = self.ordered()
        done = sum(r["status"] != "new" for r in rows)
        matched = sum(on_topic(r) for r in rows)
        lines = [
            "# Literature index",
            "",
            "Generated by `python scripts/lit.py index`. Do not edit by hand.",
            "",
            f"{done} of {len(rows)} reviewed. {matched} mention prediction markets and come first.",
            "",
            "| Key | Status | Date | Cited | Title | Authors | Venue |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for r in rows:
            authors = r.get("authors") or []
            who = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
            title = _cell(r.get("title"))
            if (r.get("url") or "").startswith(("http://", "https://")):
                title = f"[{title}]({r['url'].replace(')', '%29').replace(' ', '%20')})"
            citations = r.get("citations")
            cells = [
                f"`{r['key']}`",
                r["status"],
                _cell((r.get("date") or "")[:10]),
                "" if citations is None else str(citations),
                title,
                _cell(who),
                _cell(r.get("venue")),
            ]
            lines.append("| " + " | ".join(cells) + " |")
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(lines) + "\n")


def on_topic(rec: dict[str, Any]) -> bool:
    """True if the title or abstract mentions prediction markets by name."""
    return bool(TOPIC.search(f"{rec.get('title') or ''} {rec.get('abstract') or ''}"))


def _cell(text: Any) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    return re.sub(r"([|\[\]<>`])", r"\\\1", cleaned)


DEFAULT_CONFIG = Path("research/literature/sources.yaml")


def harvest(
    config_path: str | Path = DEFAULT_CONFIG,
    library: Library | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Fetch every source in the config, merge the records, and rewrite the index.

    A failing source is logged and listed under `failed` in the returned summary. The library
    is saved after each source.
    """
    cfg = yaml.safe_load(Path(config_path).read_text())
    library = library or Library()
    since = (date.today() - timedelta(days=int(cfg.get("lookback_days", 1095)))).isoformat()
    limit = int(cfg.get("per_query", 50))
    jobs: list[tuple[str, Callable[[], list[Record]]]] = []
    for q in cfg.get("arxiv", []):
        jobs.append((f"arxiv: {q}", lambda q=q: fetch_arxiv(q, limit)))
    for q in cfg.get("openalex", []):
        jobs.append((f"openalex: {q}", lambda q=q: fetch_openalex(q, limit, since)))
    for q in cfg.get("semantic_scholar", []):
        jobs.append((f"s2: {q}", lambda q=q: fetch_semantic_scholar(q, limit, int(since[:4]))))
    for feed in cfg.get("feeds", []):
        jobs.append((f"feed: {feed['name']}", lambda f=feed: fetch_feed(f["url"], f["name"])))

    summary: dict[str, Any] = {"new": 0, "seen": 0, "failed": []}
    for name, job in jobs:
        try:
            records = job()
        except Exception as exc:  # noqa: BLE001 - reported in the summary, then we continue
            summary["failed"].append({"job": name, "error": f"{type(exc).__name__}: {exc}"[:200]})
            log(f"FAILED {name}: {exc}")
            continue
        added = sum(library.add(r) for r in records)
        summary["new"] += added
        summary["seen"] += len(records)
        log(f"{name}: {len(records)} fetched, {added} new")
        library.save()
    library.save()
    library.write_index()
    return summary


def main(argv: list[str] | None = None) -> int:
    """Run the literature command line and return the exit code."""
    parser = argparse.ArgumentParser(prog="python scripts/lit.py")
    sub = parser.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("harvest", help="fetch sources and rewrite INDEX.md")
    h.add_argument("--config", default=str(DEFAULT_CONFIG))
    ls = sub.add_parser("list")
    ls.add_argument("--status")
    ls.add_argument("--limit", type=int, default=50)
    sub.add_parser("index", help="regenerate research/literature/INDEX.md")
    sub.add_parser("next", help="show the next unreviewed record")
    sub.add_parser("show").add_argument("key")
    rv = sub.add_parser("review")
    rv.add_argument("key")
    rv.add_argument("--status")
    rv.add_argument("--relevance", type=int)
    rv.add_argument("--note")
    rv.add_argument("--tags")
    args = parser.parse_args(argv)

    if args.cmd == "harvest":
        print(json.dumps(harvest(args.config), indent=2))
        return 0
    lib = Library()
    if args.cmd == "list":
        rows = [r for r in lib.records.values() if not args.status or r["status"] == args.status]
        rows.sort(key=lambda r: (r.get("citations") or 0, r.get("date") or ""), reverse=True)
        for r in rows[: args.limit]:
            print(f"{r['key']}\t{r['status']}\t{r.get('date')}\t{r.get('citations')}\t{r['title']}")
        print(f"{len(rows)} records", file=sys.stderr)
    elif args.cmd == "index":
        lib.write_index()
        print(f"indexed {len(lib.records)} records")
    elif args.cmd == "next":
        rec = lib.next_new()
        print(json.dumps(rec, indent=2) if rec else "nothing left to review")
    elif args.cmd == "show":
        print(json.dumps(lib.records[args.key], indent=2))
    elif args.cmd == "review":
        fields = {
            "status": args.status,
            "relevance": args.relevance,
            "note": args.note,
            "tags": args.tags.split(",") if args.tags else None,
        }
        print(
            json.dumps(
                lib.review(args.key, **{k: v for k, v in fields.items() if v is not None}), indent=2
            )
        )
        lib.save()
        lib.write_index()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

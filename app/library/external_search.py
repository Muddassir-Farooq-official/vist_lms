from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

import httpx

# All three of these are genuine free public APIs with no API key and no
# institutional agreement required — that's a real, deliberate constraint
# (see WHO/NICE below, which are NOT this kind of API, hence not included
# here). NCBI politely asks for at most ~3 requests/sec without a
# registered API key: https://www.ncbi.nlm.nih.gov/books/NBK25497/
_PUBMED_ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
_PUBMED_ESUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
_DOAJ_SEARCH_URL = "https://doaj.org/api/search/articles/{query}"

_REQUEST_TIMEOUT_SECONDS = 8.0


@dataclass
class ExternalArticle:
    """One unified shape for a search result, regardless of which source
    it came from — the portal UI renders these identically with just a
    source badge, rather than needing per-source display logic."""

    source: str  # "pubmed" | "pmc" | "doaj"
    title: str
    authors: str  # pre-joined display string, e.g. "Khan A, Ahmed S, et al."
    journal: str | None
    year: str | None
    url: str
    snippet: str | None = None


class ExternalSearchError(Exception):
    """Raised when a source's API call itself fails (network error,
    non-2xx, unparseable response) — callers catch this per-source so one
    failing provider doesn't take down a combined search."""


async def _pubmed_family_search(
    *, query: str, db: str, source_label: str, max_results: int
) -> list[ExternalArticle]:
    """Shared implementation for PubMed (db=pubmed) and PubMed Central
    (db=pmc) — same two-step E-utilities flow (esearch for IDs, esummary
    for metadata), just a different database and result link format."""
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
        try:
            search_resp = await client.get(
                _PUBMED_ESEARCH_URL,
                params={"db": db, "term": query, "retmax": max_results, "retmode": "json"},
            )
            search_resp.raise_for_status()
            id_list = search_resp.json().get("esearchresult", {}).get("idlist", [])
            if not id_list:
                return []

            summary_resp = await client.get(
                _PUBMED_ESUMMARY_URL,
                params={"db": db, "id": ",".join(id_list), "retmode": "json"},
            )
            summary_resp.raise_for_status()
            summary_data = summary_resp.json().get("result", {})
        except (httpx.HTTPError, ValueError) as exc:
            raise ExternalSearchError(f"{source_label} search failed: {exc}") from exc

    articles: list[ExternalArticle] = []
    for uid in id_list:
        doc = summary_data.get(uid)
        if not doc:
            continue
        authors_list = doc.get("authors") or []
        author_names = [a.get("name", "") for a in authors_list if a.get("name")]
        authors_display = ", ".join(author_names[:3])
        if len(author_names) > 3:
            authors_display += ", et al."

        if db == "pmc":
            url = f"https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{uid}/"
        else:
            url = f"https://pubmed.ncbi.nlm.nih.gov/{uid}/"

        articles.append(
            ExternalArticle(
                source=source_label,
                title=doc.get("title", "").rstrip(".") or "(untitled)",
                authors=authors_display or "Unknown authors",
                journal=doc.get("fulljournalname") or doc.get("source"),
                year=(doc.get("pubdate") or "").split(" ")[0] or None,
                url=url,
            )
        )
    return articles


async def search_pubmed(query: str, max_results: int = 10) -> list[ExternalArticle]:
    return await _pubmed_family_search(query=query, db="pubmed", source_label="pubmed", max_results=max_results)


async def search_pmc(query: str, max_results: int = 10) -> list[ExternalArticle]:
    return await _pubmed_family_search(query=query, db="pmc", source_label="pmc", max_results=max_results)


async def search_doaj(query: str, max_results: int = 10) -> list[ExternalArticle]:
    """DOAJ's public search API — no key required.
    Docs: https://doaj.org/api/docs"""
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
        try:
            resp = await client.get(
                _DOAJ_SEARCH_URL.format(query=quote(query, safe="")),
                params={"pageSize": max_results},
            )
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ExternalSearchError(f"DOAJ search failed: {exc}") from exc

    articles: list[ExternalArticle] = []
    for item in data.get("results", []):
        bib = item.get("bibjson", {})
        title = bib.get("title") or "(untitled)"
        author_names = [a.get("name", "") for a in (bib.get("author") or []) if a.get("name")]
        authors_display = ", ".join(author_names[:3])
        if len(author_names) > 3:
            authors_display += ", et al."

        links = bib.get("link") or []
        url = next((l.get("url") for l in links if l.get("type") == "fulltext"), None)
        if not url:
            url = next((l.get("url") for l in links if l.get("url")), None)
        if not url:
            doaj_id = item.get("id")
            url = f"https://doaj.org/article/{doaj_id}" if doaj_id else "https://doaj.org"

        journal = (bib.get("journal") or {}).get("title")
        abstract = bib.get("abstract")

        articles.append(
            ExternalArticle(
                source="doaj",
                title=title,
                authors=authors_display or "Unknown authors",
                journal=journal,
                year=str(bib.get("year")) if bib.get("year") else None,
                url=url,
                snippet=(abstract[:280] + "...") if abstract and len(abstract) > 280 else abstract,
            )
        )
    return articles
from __future__ import annotations

from pydantic import BaseModel


class ExternalArticleOut(BaseModel):
    source: str
    title: str
    authors: str
    journal: str | None
    year: str | None
    url: str
    snippet: str | None = None


class JournalSearchResponse(BaseModel):
    query: str
    results: list[ExternalArticleOut]
    # Per-source errors (e.g. one provider timed out) — surfaced rather
    # than silently swallowed, so the portal can show "DOAJ is
    # unavailable right now" instead of just quietly returning fewer
    # results with no explanation.
    source_errors: dict[str, str] = {}


class CuratedResourceOut(BaseModel):
    name: str
    description: str
    url: str
    category: str
    searchable_in_app: bool
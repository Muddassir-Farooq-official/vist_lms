from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Query

from app.auth.permissions import Permissions
from app.auth.schemas import CurrentUser
from app.core.dependencies import require_permission
from app.library.eresources_schemas import CuratedResourceOut, ExternalArticleOut, JournalSearchResponse
from app.library.external_search import ExternalSearchError, search_doaj, search_pmc, search_pubmed

router = APIRouter(prefix="/library/journals", tags=["library"])

_SOURCE_FUNCS = {
    "pubmed": search_pubmed,
    "pmc": search_pmc,
    "doaj": search_doaj,
}

# Curated, hand-maintained list — WHO and NICE do not have a free public
# search API the way PubMed/PMC/DOAJ do (WHO has no general public
# content-search API at all; NICE's syndication API requires a
# registered API key/agreement), so those two are direct links rather
# than in-app search results, same as the CINAHL/Cochrane institutional
# resources will be once that access exists. searchable_in_app tells the
# frontend which of these to treat as a "search here" tab vs a "here's
# where to look" link.
CURATED_RESOURCES: list[CuratedResourceOut] = [
    CuratedResourceOut(
        name="PubMed",
        description="Biomedical literature from MEDLINE, life science journals, and online books.",
        url="https://pubmed.ncbi.nlm.nih.gov/",
        category="Free / Open Access",
        searchable_in_app=True,
    ),
    CuratedResourceOut(
        name="PubMed Central (PMC)",
        description="Free full-text archive of biomedical and life sciences journal articles.",
        url="https://www.ncbi.nlm.nih.gov/pmc/",
        category="Free / Open Access",
        searchable_in_app=True,
    ),
    CuratedResourceOut(
        name="DOAJ",
        description="Directory of Open Access Journals — a curated index of peer-reviewed, open-access journals.",
        url="https://doaj.org/",
        category="Free / Open Access",
        searchable_in_app=True,
    ),
    CuratedResourceOut(
        name="WHO Publications & Guidelines",
        description="World Health Organization guidelines, reports, and technical publications.",
        url="https://www.who.int/publications",
        category="Free / Open Access",
        searchable_in_app=False,
    ),
    CuratedResourceOut(
        name="NICE Guidance",
        description="UK National Institute for Health and Care Excellence — clinical guidelines and quality standards.",
        url="https://www.nice.org.uk/guidance",
        category="Free / Open Access",
        searchable_in_app=False,
    ),
    CuratedResourceOut(
        name="CINAHL",
        description="Nursing and allied health literature database. Requires institutional subscription — not yet configured for VIST.",
        url="https://www.ebsco.com/products/research-databases/cinahl-database",
        category="Institutional (not yet available)",
        searchable_in_app=False,
    ),
    CuratedResourceOut(
        name="Cochrane Library",
        description="Systematic reviews and clinical trial evidence. Requires institutional/HEC-negotiated access — not yet configured for VIST.",
        url="https://www.cochranelibrary.com/",
        category="Institutional (not yet available)",
        searchable_in_app=False,
    ),
]


@router.get(
    "/search",
    response_model=JournalSearchResponse,
    summary="Live search across PubMed, PubMed Central, and DOAJ — no local catalogue, nothing manually curated",
)
async def search_journals(
    q: str = Query(min_length=2, max_length=200),
    sources: list[str] = Query(default=["pubmed", "pmc", "doaj"]),
    max_results: int = Query(default=10, ge=1, le=25),
    current_user: CurrentUser = Depends(require_permission(Permissions.LIBRARY_VIEW)),
) -> JournalSearchResponse:
    """Fans out to each requested source concurrently. A source that
    fails (timeout, rate limit, transient error) doesn't take down the
    whole search — its error is reported in source_errors and the other
    sources' results still come back."""
    unknown = [s for s in sources if s not in _SOURCE_FUNCS]
    valid_sources = [s for s in sources if s in _SOURCE_FUNCS] or list(_SOURCE_FUNCS.keys())

    tasks = {name: _SOURCE_FUNCS[name](q, max_results) for name in valid_sources}
    results_by_source = await asyncio.gather(*tasks.values(), return_exceptions=True)

    all_results: list[ExternalArticleOut] = []
    source_errors: dict[str, str] = {}
    for name, outcome in zip(tasks.keys(), results_by_source):
        if isinstance(outcome, ExternalSearchError):
            source_errors[name] = str(outcome)
        elif isinstance(outcome, Exception):
            source_errors[name] = f"Unexpected error: {outcome}"
        else:
            all_results.extend(ExternalArticleOut(**a.__dict__) for a in outcome)

    for name in unknown:
        source_errors[name] = f"Unknown source '{name}' — valid sources are {list(_SOURCE_FUNCS.keys())}."

    return JournalSearchResponse(query=q, results=all_results, source_errors=source_errors)


@router.get(
    "/e-resources",
    response_model=list[CuratedResourceOut],
    summary="Curated list of free/open-access and (future) institutional research resources",
)
async def list_curated_resources(
    current_user: CurrentUser = Depends(require_permission(Permissions.LIBRARY_VIEW)),
) -> list[CuratedResourceOut]:
    return CURATED_RESOURCES
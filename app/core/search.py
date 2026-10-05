from __future__ import annotations

from sqlalchemy import and_, false, or_


def _escape_like(term: str) -> str:
    """Make %, _ and the escape character itself literal inside an
    ILIKE pattern — without this, typing '50%' or 'a_b' in a search box
    silently acts as a wildcard."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def name_search_condition(first_name_col, last_name_col, query: str):
    """Match a person by (part of) their name, however the user types it.

    Every whitespace-separated word in the query must appear in the
    first OR the last name, so 'Ali Hassan', 'Hassan Ali', 'ali', and
    'has' all find first_name='Ali', last_name='Hassan'. Searching each
    column against the WHOLE query string (the previous behavior in the
    student, employee, and library-borrower searches) could never match
    a two-word query, since neither column alone contains the space.

    Each word is still a plain per-column substring match, so the
    per-column trigram indexes keep working.
    """
    terms = [t for t in query.split() if t]
    if not terms:
        return false()
    return and_(
        *[
            or_(
                first_name_col.ilike(f"%{_escape_like(term)}%", escape="\\"),
                last_name_col.ilike(f"%{_escape_like(term)}%", escape="\\"),
            )
            for term in terms
        ]
    )
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.dependencies import get_current_user
from app.core.reference_data import MAJOR_CITIES, PAKISTAN_DISTRICTS_BY_PROVINCE, PAKISTAN_PROVINCES, RELIGIONS

router = APIRouter(prefix="/reference-data", tags=["reference-data"])


@router.get(
    "",
    summary="Dropdown reference data (provinces, districts by province, cities, religions) — single source of truth for every form that needs these",
)
async def get_reference_data(current_user=Depends(get_current_user)) -> dict:
    """Gated on plain authentication (any logged-in user), not a
    specific permission — this is static configuration data, not
    anyone's personal record; every role's forms need it (Add/Edit
    Student, Add/Edit Employee, self-service profile pages)."""
    return {
        "provinces": PAKISTAN_PROVINCES,
        "districts_by_province": PAKISTAN_DISTRICTS_BY_PROVINCE,
        "cities": MAJOR_CITIES,
        "religions": RELIGIONS,
    }
"""Shared pagination — every list endpoint must use this (Section 25).

No endpoint may return an unbounded row set.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Sequence, TypeVar

from fastapi import Query
from pydantic import BaseModel

T = TypeVar("T")

MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 25


class PageParams(BaseModel):
    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def page_params(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
) -> PageParams:
    return PageParams(page=page, page_size=page_size)


@dataclass
class RawPage(Generic[T]):
    """Internal, service-layer pagination container. Deliberately NOT a
    Pydantic model — wrapping raw SQLAlchemy ORM instances (e.g. `Student`)
    in a Pydantic generic forces Pydantic to build a validation schema for
    the ORM class itself and crashes. Routers convert `RawPage[OrmModel]`
    into a `Page[SomeResponseSchema]` (below) using the appropriate
    role-scoped Pydantic schema before returning it."""

    items: Sequence[T]
    total: int
    page: int
    page_size: int


class Page(BaseModel, Generic[T]):
    """API response shape — only ever parameterized with a Pydantic schema,
    never an ORM model directly."""

    items: Sequence[T]
    total: int
    page: int
    page_size: int

    @property
    def total_pages(self) -> int:
        return (self.total + self.page_size - 1) // self.page_size if self.page_size else 0

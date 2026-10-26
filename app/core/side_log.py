"""Records that must survive a failed request.

A request's database work is rolled back when it ends in an error. That is right for business data, but wrong for
evidence of the failure itself: a wrong password, a blocked sign-in or a wrong reset code raise an error, so rows
written in the request session vanished (audit SEC-03). These helpers write such rows in a second, short session on
the same database connection pool and commit it at once, whatever happens to the request afterwards.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession


@asynccontextmanager
async def committed_now(session: AsyncSession) -> AsyncIterator[AsyncSession]:
    """A separate session bound to the same engine; committed on leaving the block (rolled back on error)."""
    side = AsyncSession(bind=session.bind, expire_on_commit=False)
    try:
        yield side
        await side.commit()
    except Exception:
        await side.rollback()
        raise
    finally:
        await side.close()

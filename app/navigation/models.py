from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class NavShortcut(Base):
    """A page one person starred in one portal's menu (shown under "My shortcuts" at the top of that menu)."""
    __tablename__ = "nav_shortcuts"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    portal: Mapped[str] = mapped_column(String(20), primary_key=True)       # admin, accounts, exam, faculty ... (the URL's 2nd part)
    slug: Mapped[str] = mapped_column(String(40), primary_key=True)         # the menu entry (app/main.py _nav slug)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

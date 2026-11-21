"""The server's health, written every minute by the server itself (scripts/server/health.py via `sites.sh jobs`).
The web app only reads it. One row ("latest"): the shared server figures and this institute's own site (the owner
institute, Vertex, gets every institute)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ServerHealth(Base):
    __tablename__ = "server_health"

    name: Mapped[str] = mapped_column(String(20), primary_key=True)
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)

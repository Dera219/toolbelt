from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.modules.identity.models import utcnow


class Message(Base):
    """Chat message in a (job, worker) thread between the job's customer and one
    worker. Contact info is masked at write time until that worker is booked
    (anti-disintermediation, ARCHITECTURE.md §6/§12)."""

    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_thread", "job_id", "worker_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))
    worker_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    sender_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.modules.identity.models import utcnow


class DisputeReason(str, enum.Enum):
    WORK_NOT_DONE = "work_not_done"
    QUALITY = "quality"
    DAMAGE = "damage"
    NO_SHOW = "no_show"
    OVERCHARGED = "overcharged"
    UNSAFE = "unsafe"
    OTHER = "other"


class DisputeStatus(str, enum.Enum):
    OPEN = "open"
    RESOLVED = "resolved"


class DisputeOutcome(str, enum.Enum):
    DISMISSED = "dismissed"  # claim not upheld; nothing refunded
    PARTIAL_REFUND = "partial_refund"
    FULL_REFUND = "full_refund"


class Dispute(Base):
    """A contested job. One open dispute per job at a time; resolution is an
    admin action that optionally issues a refund and always restores the job to
    the status it held before the dispute (ARCHITECTURE.md §6)."""

    __tablename__ = "disputes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    opened_by: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    against_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[DisputeReason] = mapped_column(Enum(DisputeReason))
    detail: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[DisputeStatus] = mapped_column(
        Enum(DisputeStatus), default=DisputeStatus.OPEN, index=True
    )
    # Status the job held when the dispute opened, restored on resolution.
    previous_job_status: Mapped[str] = mapped_column(String(20))
    outcome: Mapped[DisputeOutcome | None] = mapped_column(Enum(DisputeOutcome), nullable=True)
    resolution_note: Mapped[str] = mapped_column(Text, default="")
    refunded_cents: Mapped[int] = mapped_column(Integer, default=0)
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

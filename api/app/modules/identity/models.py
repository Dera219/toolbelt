import enum
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UserRole(str, enum.Enum):
    CUSTOMER = "customer"
    WORKER = "worker"
    BOTH = "both"


class UserStatus(str, enum.Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class VettingStatus(str, enum.Enum):
    UNVERIFIED = "unverified"
    PENDING = "pending"
    VERIFIED = "verified"
    REJECTED = "rejected"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(20), unique=True, nullable=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    full_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.CUSTOMER)
    status: Mapped[UserStatus] = mapped_column(Enum(UserStatus), default=UserStatus.ACTIVE)
    phone_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    locale: Mapped[str] = mapped_column(String(10), default="en")
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    worker_profile: Mapped["WorkerProfile | None"] = relationship(
        back_populates="user", uselist=False
    )

    @property
    def can_work(self) -> bool:
        return self.role in (UserRole.WORKER, UserRole.BOTH)


class PhoneVerification(Base):
    """Short-lived OTP challenge. The code itself is never stored — only its HMAC."""

    __tablename__ = "phone_verifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    phone: Mapped[str] = mapped_column(String(20))
    code_hash: Mapped[str] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorkerProfile(Base):
    __tablename__ = "worker_profiles"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    trade: Mapped[str] = mapped_column(String(40), index=True)
    bio: Mapped[str] = mapped_column(Text, default="")
    hourly_rate_cents: Mapped[int] = mapped_column(Integer)
    base_lat: Mapped[float] = mapped_column(Float, index=True)
    base_lng: Mapped[float] = mapped_column(Float, index=True)
    service_radius_km: Mapped[float] = mapped_column(Float, default=25.0)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True)
    has_own_tools: Mapped[bool] = mapped_column(Boolean, default=True)
    has_vehicle: Mapped[bool] = mapped_column(Boolean, default=False)
    vetting_status: Mapped[VettingStatus] = mapped_column(
        Enum(VettingStatus), default=VettingStatus.UNVERIFIED
    )
    rating_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    jobs_completed: Mapped[int] = mapped_column(Integer, default=0)

    user: Mapped[User] = relationship(back_populates="worker_profile")

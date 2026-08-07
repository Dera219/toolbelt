import re

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.modules.catalog.trades import ALL_TRADES
from app.modules.identity.models import UserRole, VettingStatus

_E164 = re.compile(r"^\+[1-9]\d{6,14}$")


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    full_name: str = Field(min_length=1, max_length=120)
    role: UserRole = UserRole.CUSTOMER
    phone: str | None = None
    locale: str = Field(default="en", max_length=10)
    currency: str = Field(default="USD", min_length=3, max_length=3)

    @field_validator("phone")
    @classmethod
    def phone_must_be_e164(cls, v: str | None) -> str | None:
        if v is not None and not _E164.match(v):
            raise ValueError("phone must be E.164 format, e.g. +14155550123")
        return v

    @field_validator("currency")
    @classmethod
    def currency_upper(cls, v: str) -> str:
        return v.upper()


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    full_name: str
    role: UserRole
    locale: str
    currency: str
    phone: str | None
    phone_verified: bool


class PhoneRequestIn(BaseModel):
    phone: str

    @field_validator("phone")
    @classmethod
    def phone_must_be_e164(cls, v: str) -> str:
        if not _E164.match(v):
            raise ValueError("phone must be E.164 format, e.g. +14155550123")
        return v


class PhoneVerifyIn(BaseModel):
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


class WorkerProfileIn(BaseModel):
    trade: str
    bio: str = Field(default="", max_length=2000)
    hourly_rate_cents: int = Field(gt=0, le=1_000_000)
    base_lat: float = Field(ge=-90, le=90)
    base_lng: float = Field(ge=-180, le=180)
    service_radius_km: float = Field(default=25.0, gt=0, le=100)
    is_available: bool = True
    has_own_tools: bool = True
    has_vehicle: bool = False

    @field_validator("trade")
    @classmethod
    def trade_must_exist(cls, v: str) -> str:
        if v not in ALL_TRADES:
            raise ValueError(f"unknown trade; valid trades: {sorted(ALL_TRADES)}")
        return v


class WorkerProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: int
    trade: str
    bio: str
    hourly_rate_cents: int
    base_lat: float
    base_lng: float
    service_radius_km: float
    is_available: bool
    has_own_tools: bool
    has_vehicle: bool
    vetting_status: VettingStatus
    rating_avg: float | None
    jobs_completed: int

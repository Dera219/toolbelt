from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.catalog.trades import LAUNCH_TRADES, is_bookable
from app.modules.jobs.models import JobStatus, OfferStatus


class JobCreateIn(BaseModel):
    trade: str
    title: str = Field(min_length=1, max_length=140)
    description: str = Field(default="", max_length=5000)
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    address_text: str = Field(min_length=1, max_length=300)
    scheduled_for: datetime | None = None
    budget_cents: int | None = Field(default=None, gt=0, le=100_000_000)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    customer_provides_supplies: bool = False

    @field_validator("trade")
    @classmethod
    def trade_must_be_bookable(cls, v: str) -> str:
        if not is_bookable(v):
            raise ValueError(
                f"trade not bookable yet; currently bookable: {sorted(LAUNCH_TRADES)}"
            )
        return v

    @field_validator("currency")
    @classmethod
    def currency_upper(cls, v: str) -> str:
        return v.upper()


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_id: int
    trade: str
    title: str
    description: str
    lat: float
    lng: float
    address_text: str
    scheduled_for: datetime | None
    budget_cents: int | None
    currency: str
    customer_provides_supplies: bool
    status: JobStatus
    assigned_worker_id: int | None
    created_at: datetime


class NearbyJobOut(JobOut):
    distance_km: float


class OfferCreateIn(BaseModel):
    price_cents: int = Field(gt=0, le=100_000_000)
    message: str = Field(default="", max_length=2000)


class OfferOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: int
    worker_id: int
    price_cents: int
    message: str
    status: OfferStatus
    created_at: datetime

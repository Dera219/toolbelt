from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.trust.models import DisputeOutcome, DisputeReason, DisputeStatus


class DisputeOpenIn(BaseModel):
    reason: DisputeReason
    detail: str = Field(default="", max_length=4000)


class DisputeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: int
    opened_by: int
    against_id: int
    reason: DisputeReason
    detail: str
    status: DisputeStatus
    outcome: DisputeOutcome | None
    resolution_note: str
    refunded_cents: int
    created_at: datetime
    resolved_at: datetime | None


class DisputeResolveIn(BaseModel):
    outcome: DisputeOutcome
    note: str = Field(default="", max_length=2000)
    # Required for PARTIAL_REFUND; ignored otherwise.
    refund_cents: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _partial_needs_amount(self) -> "DisputeResolveIn":
        if self.outcome == DisputeOutcome.PARTIAL_REFUND and self.refund_cents is None:
            raise ValueError("refund_cents is required for a partial refund")
        return self

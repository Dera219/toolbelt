from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RatingCreateIn(BaseModel):
    stars: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=2000)


class RatingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: int
    rater_id: int
    ratee_id: int
    stars: int
    comment: str
    created_at: datetime


class JobRatingsOut(BaseModel):
    """What the requester is allowed to see for a job's ratings."""

    mine: RatingOut | None
    other: RatingOut | None  # null until revealed (both submitted or window elapsed)
    other_submitted: bool

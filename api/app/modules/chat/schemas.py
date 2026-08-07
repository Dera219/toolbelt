from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MessageCreateIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: int
    worker_id: int
    sender_id: int
    body: str
    created_at: datetime

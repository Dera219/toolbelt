"""Admin trust & safety endpoints. Admin accounts are provisioned operationally
(direct DB flag), never via the public API."""

import enum
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import require_admin
from app.modules.identity.models import User, VettingStatus, WorkerProfile
from app.modules.identity.schemas import WorkerProfileOut

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])

DbDep = Annotated[Session, Depends(get_db)]
AdminUser = Annotated[User, Depends(require_admin)]


class VettingDecision(str, enum.Enum):
    VERIFIED = "verified"
    REJECTED = "rejected"


class VettingDecisionIn(BaseModel):
    decision: VettingDecision
    note: str = Field(default="", max_length=1000)


@router.get("/vetting/queue", response_model=list[WorkerProfileOut])
def vetting_queue(db: DbDep, limit: int = 50):
    stmt = (
        select(WorkerProfile)
        .where(WorkerProfile.vetting_status == VettingStatus.PENDING)
        .limit(min(limit, 100))
    )
    return list(db.scalars(stmt))


@router.post("/workers/{user_id}/vetting", response_model=WorkerProfileOut)
def decide_vetting(user_id: int, body: VettingDecisionIn, admin: AdminUser, db: DbDep):
    profile = db.get(WorkerProfile, user_id)
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Worker profile not found")
    if profile.vetting_status != VettingStatus.PENDING:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Worker is not pending vetting")
    profile.vetting_status = VettingStatus(body.decision.value)
    db.flush()
    return profile

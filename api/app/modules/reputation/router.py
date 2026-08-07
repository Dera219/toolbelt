from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import get_current_user
from app.modules.identity.models import User
from app.modules.reputation import service
from app.modules.reputation.schemas import JobRatingsOut, RatingCreateIn, RatingOut

router = APIRouter()

DbDep = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]


@router.post(
    "/jobs/{job_id}/ratings", response_model=RatingOut, status_code=status.HTTP_201_CREATED
)
def rate_job(job_id: int, body: RatingCreateIn, user: CurrentUser, db: DbDep):
    return service.create_rating(db, user, job_id, body)


@router.get("/jobs/{job_id}/ratings", response_model=JobRatingsOut)
def job_ratings(job_id: int, user: CurrentUser, db: DbDep):
    return service.get_job_ratings_for(db, user, job_id)

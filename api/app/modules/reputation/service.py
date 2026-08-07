from datetime import timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.identity.models import User, WorkerProfile, utcnow
from app.modules.jobs.models import Job, JobStatus
from app.modules.reputation.models import Rating
from app.modules.reputation.schemas import RatingCreateIn

REVEAL_AFTER = timedelta(days=14)


def _get_rateable_job(db: Session, job_id: int, actor: User) -> tuple[Job, int]:
    """Return (job, counterparty_id) if the actor may rate this job."""
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Job not found")
    if actor.id not in (job.customer_id, job.assigned_worker_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not a party to this job")
    if job.status != JobStatus.COMPLETED:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Job is not completed")
    counterparty = (
        job.assigned_worker_id if actor.id == job.customer_id else job.customer_id
    )
    return job, counterparty


def create_rating(db: Session, actor: User, job_id: int, body: RatingCreateIn) -> Rating:
    job, counterparty = _get_rateable_job(db, job_id, actor)
    existing = db.scalar(
        select(Rating.id).where(Rating.job_id == job.id, Rating.rater_id == actor.id)
    )
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="You already rated this job")
    rating = Rating(
        job_id=job.id,
        rater_id=actor.id,
        ratee_id=counterparty,
        stars=body.stars,
        comment=body.comment,
    )
    db.add(rating)
    db.flush()
    _refresh_worker_aggregate(db, counterparty)
    return rating


def _refresh_worker_aggregate(db: Session, ratee_id: int) -> None:
    profile = db.get(WorkerProfile, ratee_id)
    if profile is None:
        return  # ratee is a customer; per-user customer scores come later
    profile.rating_avg = db.scalar(
        select(func.avg(Rating.stars)).where(Rating.ratee_id == ratee_id)
    )


def _is_revealed(rating: Rating, both_submitted: bool) -> bool:
    if both_submitted:
        return True
    created = rating.created_at
    if created.tzinfo is None:  # SQLite drops tzinfo; stored values are UTC
        created = created.replace(tzinfo=timezone.utc)
    return utcnow() - created >= REVEAL_AFTER


def get_job_ratings_for(db: Session, actor: User, job_id: int) -> dict:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Job not found")
    if actor.id not in (job.customer_id, job.assigned_worker_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not a party to this job")
    ratings = list(db.scalars(select(Rating).where(Rating.job_id == job.id)))
    mine = next((r for r in ratings if r.rater_id == actor.id), None)
    other = next((r for r in ratings if r.rater_id != actor.id), None)
    both = mine is not None and other is not None
    return {
        "mine": mine,
        "other": other if (other is not None and _is_revealed(other, both)) else None,
        "other_submitted": other is not None,
    }

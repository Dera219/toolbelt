import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import get_current_user
from app.modules.identity.models import User
from app.modules.jobs import service
from app.modules.jobs.models import Job, Offer
from app.modules.jobs.schemas import (
    JobCreateIn,
    JobOut,
    NearbyJobOut,
    OfferCreateIn,
    OfferOut,
    PhotoOut,
)

router = APIRouter()

DbDep = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]


@router.post("/jobs", response_model=JobOut, status_code=status.HTTP_201_CREATED)
def create_job(body: JobCreateIn, user: CurrentUser, db: DbDep):
    return service.create_job(db, user, body)


@router.get("/jobs/nearby", response_model=list[NearbyJobOut])
def nearby_jobs(
    user: CurrentUser,
    db: DbDep,
    lat: Annotated[float, Query(ge=-90, le=90)],
    lng: Annotated[float, Query(ge=-180, le=180)],
    radius_km: Annotated[float | None, Query(gt=0)] = None,
    trade: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    settings = get_settings()
    radius = min(radius_km or settings.default_search_radius_km, settings.max_search_radius_km)
    pairs = service.find_nearby_jobs(db, user, lat, lng, radius, trade, limit)
    return [
        NearbyJobOut(**JobOut.model_validate(job).model_dump(), distance_km=round(dist, 2))
        for job, dist in pairs
    ]


@router.get("/jobs/mine", response_model=list[JobOut])
def my_jobs(
    user: CurrentUser,
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    stmt = (
        select(Job)
        .where((Job.customer_id == user.id) | (Job.assigned_worker_id == user.id))
        .order_by(Job.created_at.desc(), Job.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(db.scalars(stmt))


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: int, user: CurrentUser, db: DbDep):
    return service.get_job_for_viewer(db, job_id, user)


_PHOTO_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
_MAX_PHOTO_BYTES = 8 * 1024 * 1024
_MAX_PHOTOS_PER_JOB = 8


@router.post(
    "/jobs/{job_id}/photos", response_model=PhotoOut, status_code=status.HTTP_201_CREATED
)
async def upload_job_photo(job_id: int, file: UploadFile, user: CurrentUser, db: DbDep):
    from app.core.storage import get_storage
    from app.modules.jobs.models import JobPhoto, JobStatus

    job = service.get_job_for_viewer(db, job_id, user)
    if job.customer_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Only the job owner adds photos")
    if job.status not in (JobStatus.OPEN, JobStatus.ASSIGNED, JobStatus.IN_PROGRESS):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Job is closed")
    if len(job.photos) >= _MAX_PHOTOS_PER_JOB:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Photo limit reached")
    ext = _PHOTO_TYPES.get(file.content_type or "")
    if ext is None:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Use JPEG, PNG, or WebP"
        )
    data = await file.read()
    if len(data) > _MAX_PHOTO_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Photo too large (8MB max)"
        )
    key = f"jobs/{job.id}/{uuid.uuid4().hex}.{ext}"
    get_storage().save(key, data)
    photo = JobPhoto(job_id=job.id, storage_key=key, content_type=file.content_type or "")
    db.add(photo)
    db.flush()
    return photo


@router.post("/jobs/{job_id}/offers", response_model=OfferOut, status_code=status.HTTP_201_CREATED)
def make_offer(job_id: int, body: OfferCreateIn, user: CurrentUser, db: DbDep):
    return service.make_offer(db, user, job_id, body)


@router.get("/jobs/{job_id}/offers", response_model=list[OfferOut])
def list_offers(job_id: int, user: CurrentUser, db: DbDep):
    job = service.get_job_for_viewer(db, job_id, user)
    stmt = select(Offer).where(Offer.job_id == job.id)
    if job.customer_id != user.id:
        stmt = stmt.where(Offer.worker_id == user.id)  # workers see only their own offer
    return list(db.scalars(stmt))


@router.post("/offers/{offer_id}/accept", response_model=OfferOut)
def accept_offer(offer_id: int, user: CurrentUser, db: DbDep):
    return service.accept_offer(db, user, offer_id)


@router.post("/jobs/{job_id}/start", response_model=JobOut)
def start_job(job_id: int, user: CurrentUser, db: DbDep):
    return service.start_job(db, user, job_id)


@router.post("/jobs/{job_id}/complete", response_model=JobOut)
def complete_job(job_id: int, user: CurrentUser, db: DbDep):
    return service.complete_job(db, user, job_id)


@router.post("/jobs/{job_id}/cancel", response_model=JobOut)
def cancel_job(job_id: int, user: CurrentUser, db: DbDep):
    return service.cancel_job(db, user, job_id)

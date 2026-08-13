"""Jobs domain service. All multi-row mutations happen here, inside the request's
transaction (get_db commits on success, rolls back on any exception)."""

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.modules.identity.models import User, VettingStatus, WorkerProfile
from app.modules.jobs import state
from app.modules.jobs.models import Job, JobStatus, Offer, OfferStatus
from app.modules.jobs.schemas import JobCreateIn, OfferCreateIn
from app.modules.matching.geo import bounding_box, haversine_km
from app.modules.notifications import service as notifications
from app.modules.payments import service as payments_service


def create_job(db: Session, customer: User, body: JobCreateIn) -> Job:
    job = Job(customer_id=customer.id, **body.model_dump())
    db.add(job)
    db.flush()
    notifications.notify_job_posted(db, job)
    return job


def find_nearby_jobs(
    db: Session,
    worker: User,
    lat: float,
    lng: float,
    radius_km: float,
    trade: str | None,
    limit: int,
) -> list[tuple[Job, float]]:
    min_lat, max_lat, min_lng, max_lng = bounding_box(lat, lng, radius_km)
    stmt = select(Job).where(
        Job.status == JobStatus.OPEN,
        Job.customer_id != worker.id,
        Job.lat.between(min_lat, max_lat),
        Job.lng.between(min_lng, max_lng),
    )
    if trade:
        stmt = stmt.where(Job.trade == trade)
    results: list[tuple[Job, float]] = []
    for job in db.scalars(stmt):
        dist = haversine_km(lat, lng, job.lat, job.lng)
        if dist <= radius_km:
            results.append((job, dist))
    results.sort(key=lambda pair: pair[1])
    return results[:limit]


def _get_job_or_404(db: Session, job_id: int, *, for_update: bool = False) -> Job:
    stmt = select(Job).where(Job.id == job_id)
    if for_update and db.bind.dialect.name != "sqlite":
        stmt = stmt.with_for_update()
    job = db.scalar(stmt)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job


def get_job_for_viewer(db: Session, job_id: int, viewer: User) -> Job:
    job = _get_job_or_404(db, job_id)
    is_party = viewer.id in (job.customer_id, job.assigned_worker_id)
    if job.status != JobStatus.OPEN and not is_party:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job


def make_offer(db: Session, worker: User, job_id: int, body: OfferCreateIn) -> Offer:
    if not worker.can_work:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Account cannot make offers")
    profile = db.get(WorkerProfile, worker.id)
    if profile is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Create a worker profile first")
    if profile.vetting_status != VettingStatus.VERIFIED:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="Complete vetting before making offers"
        )
    job = _get_job_or_404(db, job_id, for_update=True)
    if job.customer_id == worker.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Cannot offer on your own job")
    if job.status != JobStatus.OPEN:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Job is no longer open")
    if job.trade != profile.trade:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Job trade does not match yours")
    existing = db.scalar(
        select(Offer).where(Offer.job_id == job.id, Offer.worker_id == worker.id)
    )
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="You already offered on this job")
    offer = Offer(job_id=job.id, worker_id=worker.id, **body.model_dump())
    db.add(offer)
    db.flush()
    notifications.notify_offer_received(db, job, offer.price_cents)
    return offer


def accept_offer(db: Session, customer: User, offer_id: int) -> Offer:
    offer = db.get(Offer, offer_id)
    if offer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Offer not found")
    job = _get_job_or_404(db, offer.job_id, for_update=True)
    if job.customer_id != customer.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Only the job owner can accept")
    if job.status != JobStatus.OPEN or offer.status != OfferStatus.PENDING:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Offer is no longer acceptable")

    offer.status = OfferStatus.ACCEPTED
    # Atomically expire all sibling offers (invariant: one accepted offer per job).
    db.execute(
        update(Offer)
        .where(
            Offer.job_id == job.id,
            Offer.id != offer.id,
            Offer.status == OfferStatus.PENDING,
        )
        .values(status=OfferStatus.EXPIRED)
    )
    job.assigned_worker_id = offer.worker_id
    state.transition(db, job, JobStatus.ASSIGNED, actor_id=customer.id, note=f"offer {offer.id}")
    # Authorize the customer's card in the same transaction: if the card declines,
    # the whole acceptance (assignment, sibling expiry) rolls back.
    payments_service.authorize_for_job(db, customer, job, offer.price_cents)
    db.flush()
    notifications.notify_offer_accepted(db, job, offer.worker_id)
    return offer


def _transition_endpoint(
    db: Session, actor: User, job_id: int, to_status: JobStatus, allowed_actor: str
) -> Job:
    job = _get_job_or_404(db, job_id, for_update=True)
    is_customer = job.customer_id == actor.id
    is_worker = job.assigned_worker_id == actor.id
    permitted = {
        "customer": is_customer,
        "worker": is_worker,
        "either": is_customer or is_worker,
    }[allowed_actor]
    if not permitted:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not a party to this job")
    if job.status == JobStatus.DISPUTED:
        # A dispute freezes the job for both parties. The state machine's edges
        # out of DISPUTED exist solely for trust.service.resolve_dispute; letting
        # a party use them would capture the card and pay out mid-dispute (or
        # cancel out of a captured one), leaving the dispute unresolvable.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Job is under dispute and is frozen until it is resolved",
        )
    try:
        state.transition(db, job, to_status, actor_id=actor.id)
    except state.InvalidTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc))
    db.flush()
    return job


def start_job(db: Session, worker: User, job_id: int) -> Job:
    job = _transition_endpoint(db, worker, job_id, JobStatus.IN_PROGRESS, "worker")
    notifications.notify_job_started(db, job)
    return job


def complete_job(db: Session, actor: User, job_id: int) -> Job:
    job = _transition_endpoint(db, actor, job_id, JobStatus.COMPLETED, "either")
    if job.assigned_worker_id is not None:
        profile = db.get(WorkerProfile, job.assigned_worker_id)
        if profile is not None:
            profile.jobs_completed += 1
    payments_service.capture_for_job(db, job)
    notifications.notify_job_completed(db, job)
    return job


def cancel_job(db: Session, actor: User, job_id: int) -> Job:
    job = _get_job_or_404(db, job_id)
    allowed = "customer" if job.status == JobStatus.OPEN else "either"
    job = _transition_endpoint(db, actor, job_id, JobStatus.CANCELLED, allowed)
    payments_service.release_for_job(db, job)
    notifications.notify_job_cancelled(db, job, actor_id=actor.id)
    return job

"""Disputes: opening, viewing, and admin resolution.

A dispute freezes the job in `disputed` and remembers the status it came from.
Resolving restores that status and optionally refunds — refunds always go
through the payments service so the ledger stays balanced.
"""

from datetime import timedelta

from fastapi import HTTPException, status as http
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.identity.models import User, utcnow
from app.modules.jobs import state
from app.modules.jobs.models import Job, JobStatus
from app.modules.notifications import service as notifications
from app.modules.payments import service as payments_service
from app.modules.payments.models import Payment, PaymentStatus
from app.modules.trust.models import Dispute, DisputeOutcome, DisputeStatus
from app.modules.trust.schemas import DisputeOpenIn, DisputeResolveIn

# How long after completion a job can still be disputed. Work is judged after
# it's done, so completed jobs must remain contestable — but not forever, or
# payouts could never be considered final.
DISPUTE_WINDOW = timedelta(days=3)

DISPUTABLE_FROM = (JobStatus.ASSIGNED, JobStatus.IN_PROGRESS, JobStatus.COMPLETED)


def _job_for_party(db: Session, job_id: int, actor: User) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, detail="Job not found")
    if actor.id not in (job.customer_id, job.assigned_worker_id):
        raise HTTPException(http.HTTP_403_FORBIDDEN, detail="Not a party to this job")
    return job


def open_dispute(db: Session, actor: User, job_id: int, body: DisputeOpenIn) -> Dispute:
    job = _job_for_party(db, job_id, actor)
    if job.status not in DISPUTABLE_FROM:
        raise HTTPException(
            http.HTTP_409_CONFLICT,
            detail=f"A job in '{job.status.value}' cannot be disputed",
        )
    if job.status == JobStatus.COMPLETED:
        completed_at = _completed_at(db, job)
        if completed_at is not None and utcnow() - completed_at > DISPUTE_WINDOW:
            raise HTTPException(
                http.HTTP_409_CONFLICT,
                detail="The dispute window for this job has closed",
            )
    existing = db.scalar(
        select(Dispute).where(Dispute.job_id == job.id, Dispute.status == DisputeStatus.OPEN)
    )
    if existing is not None:
        raise HTTPException(http.HTTP_409_CONFLICT, detail="This job already has an open dispute")

    against = job.assigned_worker_id if actor.id == job.customer_id else job.customer_id
    if against is None:
        raise HTTPException(http.HTTP_409_CONFLICT, detail="Job has no counterparty")

    dispute = Dispute(
        job_id=job.id,
        opened_by=actor.id,
        against_id=against,
        reason=body.reason,
        detail=body.detail,
        previous_job_status=job.status.value,
    )
    db.add(dispute)
    _force_status(db, job, JobStatus.DISPUTED, actor.id, note=f"dispute: {body.reason.value}")
    db.flush()
    notifications.notify_dispute_opened(db, job, against)
    return dispute


def _completed_at(db: Session, job: Job):
    """When the job entered `completed`, from the audit log."""
    from app.modules.jobs.models import JobEvent

    event = db.scalar(
        select(JobEvent)
        .where(JobEvent.job_id == job.id, JobEvent.to_status == JobStatus.COMPLETED.value)
        .order_by(JobEvent.created_at.desc())
        .limit(1)
    )
    if event is None:
        return None
    created = event.created_at
    if created.tzinfo is None:  # SQLite drops tzinfo; stored values are UTC
        from datetime import timezone

        created = created.replace(tzinfo=timezone.utc)
    return created


def _force_status(db: Session, job: Job, to_status: JobStatus, actor_id: int, note: str) -> None:
    try:
        state.transition(db, job, to_status, actor_id=actor_id, note=note)
    except state.InvalidTransition as exc:
        raise HTTPException(http.HTTP_409_CONFLICT, detail=str(exc))


def get_dispute_for_job(db: Session, actor: User, job_id: int) -> Dispute:
    job = _job_for_party(db, job_id, actor)
    dispute = db.scalar(
        select(Dispute).where(Dispute.job_id == job.id).order_by(Dispute.created_at.desc()).limit(1)
    )
    if dispute is None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, detail="No dispute for this job")
    return dispute


def list_open_disputes(db: Session, limit: int) -> list[Dispute]:
    return list(
        db.scalars(
            select(Dispute)
            .where(Dispute.status == DisputeStatus.OPEN)
            .order_by(Dispute.created_at)
            .limit(limit)
        )
    )


def resolve_dispute(db: Session, admin: User, dispute_id: int, body: DisputeResolveIn) -> Dispute:
    dispute = db.get(Dispute, dispute_id)
    if dispute is None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, detail="Dispute not found")
    if dispute.status != DisputeStatus.OPEN:
        raise HTTPException(http.HTTP_409_CONFLICT, detail="Dispute is already resolved")

    job = db.get(Job, dispute.job_id)
    payment = db.scalar(select(Payment).where(Payment.job_id == dispute.job_id))

    refunded = 0
    if body.outcome != DisputeOutcome.DISMISSED:
        if payment is None:
            raise HTTPException(
                http.HTTP_409_CONFLICT, detail="No payment on this job to refund"
            )
        if payment.status not in (PaymentStatus.CAPTURED, PaymentStatus.PAID_OUT):
            raise HTTPException(
                http.HTTP_409_CONFLICT,
                detail=f"Payment in '{payment.status.value}' cannot be refunded",
            )
        amount = (
            payment.amount_cents - payment.refunded_cents
            if body.outcome == DisputeOutcome.FULL_REFUND
            else body.refund_cents
        )
        payments_service.refund_payment(db, payment.id, amount)
        refunded = amount or 0

    dispute.status = DisputeStatus.RESOLVED
    dispute.outcome = body.outcome
    dispute.resolution_note = body.note
    dispute.refunded_cents = refunded
    dispute.resolved_by = admin.id
    dispute.resolved_at = utcnow()

    restored = JobStatus(dispute.previous_job_status)
    _force_status(
        db, job, restored, admin.id, note=f"dispute {dispute.id} resolved: {body.outcome.value}"
    )
    db.flush()
    notifications.notify_dispute_resolved(db, job, dispute.opened_by, dispute.against_id)
    return dispute

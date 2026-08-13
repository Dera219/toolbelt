"""Notification fan-out for domain events.

Every function here is best-effort and must never raise into the caller. A job
transition or a payment capture is the real work; the notification is a courtesy
on top of it. If push delivery breaks, bookings must still complete.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.modules.identity.models import (
    User,
    UserRole,
    UserStatus,
    VettingStatus,
    WorkerProfile,
    utcnow,
)
from app.modules.jobs.models import Job
from app.modules.matching.geo import bounding_box, haversine_km
from app.modules.notifications.models import DeviceToken
from app.modules.notifications.push import PushMessage, get_push_sender

logger = logging.getLogger(__name__)


def register_token(db: Session, user: User, token: str, platform: str = "unknown") -> DeviceToken:
    """Idempotently bind a device token to a user.

    A token can move between users — someone logs out and a housemate logs in on
    the same phone. Re-pointing the existing row is what keeps the previous
    user's jobs from being delivered to the new one.
    """
    existing = db.scalar(select(DeviceToken).where(DeviceToken.token == token))
    if existing is not None:
        existing.user_id = user.id
        existing.platform = platform or existing.platform
        existing.last_seen_at = utcnow()
        db.flush()
        return existing
    row = DeviceToken(user_id=user.id, token=token, platform=platform)
    db.add(row)
    db.flush()
    return row


def unregister_token(db: Session, user: User, token: str) -> None:
    row = db.scalar(
        select(DeviceToken).where(DeviceToken.token == token, DeviceToken.user_id == user.id)
    )
    if row is not None:
        db.delete(row)
        db.flush()


def _tokens_for(db: Session, user_ids: list[int]) -> dict[int, list[str]]:
    if not user_ids:
        return {}
    rows = db.scalars(select(DeviceToken).where(DeviceToken.user_id.in_(user_ids))).all()
    out: dict[int, list[str]] = {}
    for r in rows:
        out.setdefault(r.user_id, []).append(r.token)
    return out


def _dispatch(db: Session, user_ids: list[int], title: str, body: str, data: dict) -> None:
    try:
        by_user = _tokens_for(db, user_ids)
        messages = [
            PushMessage(to=token, title=title, body=body, data=data)
            for tokens in by_user.values()
            for token in tokens
        ]
        if messages:
            get_push_sender().send(messages)
    except Exception:  # noqa: BLE001 — notifications must never break the caller
        logger.exception("Push dispatch failed for %s", data)


# ---------------------------------------------------------------------------
# Domain events
# ---------------------------------------------------------------------------

def notify_job_posted(db: Session, job: Job) -> None:
    """Tell nearby verified workers in the right trade that work is available.

    This is the notification that makes the marketplace function: time-to-first
    -offer is the liquidity metric, and without this a worker only sees a job if
    they happen to open the app.

    Targeting uses each worker's own `service_radius_km` rather than a platform
    default — a worker who said they travel 10km should not be pinged about a
    job 24km away just because the platform default is 25.
    """
    settings = get_settings()
    # Coarse bounding box at the widest radius any worker could have set, then
    # an exact per-worker distance check below.
    min_lat, max_lat, min_lng, max_lng = bounding_box(
        job.lat, job.lng, settings.max_search_radius_km
    )

    candidates = db.scalars(
        select(User)
        .join(WorkerProfile, WorkerProfile.user_id == User.id)
        .where(
            # Anyone who can work (User.can_work): dual-role users take jobs too.
            User.role.in_((UserRole.WORKER, UserRole.BOTH)),
            User.status == UserStatus.ACTIVE,
            User.id != job.customer_id,
            WorkerProfile.trade == job.trade,
            WorkerProfile.is_available.is_(True),
            WorkerProfile.vetting_status == VettingStatus.VERIFIED,
            WorkerProfile.base_lat.between(min_lat, max_lat),
            WorkerProfile.base_lng.between(min_lng, max_lng),
        )
    ).all()

    targets = [
        w.id
        for w in candidates
        if haversine_km(job.lat, job.lng, w.worker_profile.base_lat, w.worker_profile.base_lng)
        <= w.worker_profile.service_radius_km
    ]

    _dispatch(
        db,
        targets,
        title=f"New {job.trade.replace('_', ' ')} job nearby",
        body=job.title,
        data={"type": "job.posted", "job_id": job.id},
    )


def notify_offer_received(db: Session, job: Job, price_cents: int) -> None:
    _dispatch(
        db,
        [job.customer_id],
        title="You have a new offer",
        body=f"${price_cents / 100:.2f} for {job.title}",
        data={"type": "offer.received", "job_id": job.id},
    )


def notify_offer_accepted(db: Session, job: Job, worker_id: int) -> None:
    _dispatch(
        db,
        [worker_id],
        title="Your offer was accepted",
        body=job.title,
        data={"type": "offer.accepted", "job_id": job.id},
    )


def notify_job_started(db: Session, job: Job) -> None:
    _dispatch(
        db,
        [job.customer_id],
        title="Work has started",
        body=job.title,
        data={"type": "job.started", "job_id": job.id},
    )


def notify_job_completed(db: Session, job: Job) -> None:
    recipients = [job.customer_id]
    if job.assigned_worker_id:
        recipients.append(job.assigned_worker_id)
    _dispatch(
        db,
        recipients,
        title="Job marked complete",
        body=f"{job.title} — leave a rating",
        data={"type": "job.completed", "job_id": job.id},
    )


def notify_job_cancelled(db: Session, job: Job, actor_id: int) -> None:
    recipients = [uid for uid in (job.customer_id, job.assigned_worker_id) if uid and uid != actor_id]
    _dispatch(
        db,
        recipients,
        title="Job cancelled",
        body=job.title,
        data={"type": "job.cancelled", "job_id": job.id},
    )


def notify_message(db: Session, job: Job, recipient_id: int, preview: str) -> None:
    _dispatch(
        db,
        [recipient_id],
        title="New message",
        body=preview[:120],
        data={"type": "chat.message", "job_id": job.id},
    )


def notify_dispute_opened(db: Session, job: Job, against_id: int) -> None:
    _dispatch(
        db,
        [against_id],
        title="Dispute opened",
        body=f"{job.title} — we'll review and be in touch.",
        data={"type": "dispute.opened", "job_id": job.id},
    )


def notify_dispute_resolved(db: Session, job: Job, opened_by: int, against_id: int) -> None:
    _dispatch(
        db,
        [opened_by, against_id],
        title="Dispute resolved",
        body=job.title,
        data={"type": "dispute.resolved", "job_id": job.id},
    )

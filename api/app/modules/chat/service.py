import re

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.identity.models import User
from app.modules.chat.models import Message
from app.modules.jobs.models import Job, Offer

MASK = "[contact hidden until booking]"
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"\+?\d(?:[\s().-]?\d){6,}")


def mask_contact_info(text: str) -> str:
    return _PHONE.sub(MASK, _EMAIL.sub(MASK, text))


def _authorize_thread(db: Session, job_id: int, worker_id: int, actor: User) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Job not found")
    is_customer = actor.id == job.customer_id
    is_thread_worker = actor.id == worker_id
    if not (is_customer or is_thread_worker):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not a party to this thread")
    # The worker side of the thread must actually be engaged with the job.
    if job.assigned_worker_id != worker_id:
        has_offer = db.scalar(
            select(Offer.id).where(Offer.job_id == job.id, Offer.worker_id == worker_id)
        )
        if has_offer is None:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, detail="No offer from this worker on this job"
            )
    return job


def send_message(db: Session, actor: User, job_id: int, worker_id: int, body: str) -> Message:
    job = _authorize_thread(db, job_id, worker_id, actor)
    text = body.strip()
    if not text:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Empty message")
    if job.assigned_worker_id != worker_id:
        text = mask_contact_info(text)  # masked at write time: never persisted unmasked
    message = Message(job_id=job.id, worker_id=worker_id, sender_id=actor.id, body=text)
    db.add(message)
    db.flush()
    return message


def list_messages(
    db: Session, actor: User, job_id: int, worker_id: int, limit: int, before_id: int | None
) -> list[Message]:
    _authorize_thread(db, job_id, worker_id, actor)
    stmt = select(Message).where(Message.job_id == job_id, Message.worker_id == worker_id)
    if before_id is not None:
        stmt = stmt.where(Message.id < before_id)
    stmt = stmt.order_by(Message.id.desc()).limit(limit)
    return list(reversed(list(db.scalars(stmt))))

"""Job lifecycle state machine — the single place status transitions are allowed.

open ──accept──► assigned ──start──► in_progress ──complete──► completed
  │                 │                     │
  └─cancel          └─cancel              └─dispute──► disputed
"""

from sqlalchemy.orm import Session

from app.modules.jobs.models import Job, JobEvent, JobStatus

_ALLOWED: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.OPEN: frozenset({JobStatus.ASSIGNED, JobStatus.CANCELLED}),
    JobStatus.ASSIGNED: frozenset(
        {JobStatus.IN_PROGRESS, JobStatus.CANCELLED, JobStatus.DISPUTED}
    ),
    JobStatus.IN_PROGRESS: frozenset({JobStatus.COMPLETED, JobStatus.DISPUTED}),
    # Work is judged after it is done, so a completed job stays contestable for
    # a bounded window (trust.service.DISPUTE_WINDOW enforces the deadline).
    JobStatus.COMPLETED: frozenset({JobStatus.DISPUTED}),
    JobStatus.CANCELLED: frozenset(),
    # Resolution restores whichever status the job held before the dispute.
    # These edges are for trust.service.resolve_dispute only — the party-facing
    # endpoints refuse to touch a disputed job (jobs.service._transition_endpoint).
    JobStatus.DISPUTED: frozenset(
        {
            JobStatus.ASSIGNED,
            JobStatus.IN_PROGRESS,
            JobStatus.COMPLETED,
            JobStatus.CANCELLED,
        }
    ),
}


class InvalidTransition(Exception):
    def __init__(self, from_status: JobStatus, to_status: JobStatus):
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(f"Cannot move job from '{from_status.value}' to '{to_status.value}'")


def transition(db: Session, job: Job, to_status: JobStatus, actor_id: int, note: str = "") -> None:
    """Apply a status transition and write the audit event. Caller owns the transaction."""
    if to_status not in _ALLOWED[job.status]:
        raise InvalidTransition(job.status, to_status)
    db.add(
        JobEvent(
            job_id=job.id,
            actor_id=actor_id,
            from_status=job.status.value,
            to_status=to_status.value,
            note=note,
        )
    )
    job.status = to_status

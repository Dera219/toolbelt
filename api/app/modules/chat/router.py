from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import get_current_user
from app.modules.chat import service
from app.modules.chat.schemas import MessageCreateIn, MessageOut
from app.modules.identity.models import User

router = APIRouter()

DbDep = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]


@router.post(
    "/jobs/{job_id}/threads/{worker_id}/messages",
    response_model=MessageOut,
    status_code=status.HTTP_201_CREATED,
)
def send_message(
    job_id: int, worker_id: int, body: MessageCreateIn, user: CurrentUser, db: DbDep
):
    return service.send_message(db, user, job_id, worker_id, body.body)


@router.get("/jobs/{job_id}/threads/{worker_id}/messages", response_model=list[MessageOut])
def list_messages(
    job_id: int,
    worker_id: int,
    user: CurrentUser,
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    before_id: Annotated[int | None, Query(ge=1)] = None,
):
    return service.list_messages(db, user, job_id, worker_id, limit, before_id)

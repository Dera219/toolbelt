from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import get_current_user
from app.modules.identity.models import User
from app.modules.notifications import service

router = APIRouter()

DbDep = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]


class DeviceTokenIn(BaseModel):
    token: str = Field(min_length=1, max_length=255)
    platform: str = Field(default="unknown", max_length=16)


@router.post("/me/device-tokens", status_code=status.HTTP_204_NO_CONTENT)
def register_device_token(body: DeviceTokenIn, user: CurrentUser, db: DbDep):
    """Register this device for push. Idempotent — safe to call on every launch."""
    service.register_token(db, user, body.token, body.platform)
    db.commit()


@router.delete("/me/device-tokens/{token}", status_code=status.HTTP_204_NO_CONTENT)
def unregister_device_token(token: str, user: CurrentUser, db: DbDep):
    """Drop a device token. Call on logout so the next user of the phone does
    not receive the previous user's job notifications."""
    service.unregister_token(db, user, token)
    db.commit()

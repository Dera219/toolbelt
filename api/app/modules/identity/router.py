from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.ratelimit import rate_limit
from app.core.security import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from app.modules.identity import otp, sessions
from app.modules.identity.models import User, VettingStatus, WorkerProfile
from app.modules.identity.schemas import (
    LoginIn,
    RefreshIn,
    PhoneRequestIn,
    PhoneVerifyIn,
    RegisterIn,
    TokenOut,
    UserOut,
    WorkerProfileIn,
    WorkerProfileOut,
)

router = APIRouter()

DbDep = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]


@router.post(
    "/auth/register",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[rate_limit("register", 20, 3600)],
)
def register(body: RegisterIn, db: DbDep):
    email = body.email.lower()
    exists = db.scalar(select(User.id).where(User.email == email))
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Email already registered")
    if body.phone:
        phone_exists = db.scalar(select(User.id).where(User.phone == body.phone))
        if phone_exists:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="Phone already registered")
    user = User(
        email=email,
        phone=body.phone,
        password_hash=hash_password(body.password),
        full_name=body.full_name.strip(),
        role=body.role,
        locale=body.locale,
        currency=body.currency,
    )
    db.add(user)
    db.flush()
    return user


@router.post(
    "/auth/login",
    response_model=TokenOut,
    dependencies=[rate_limit("login", 10, 300)],
)
def login(body: LoginIn, db: DbDep):
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    # Verify against a constant dummy hash on miss so response timing does not
    # reveal whether the email exists.
    if user is None:
        verify_password(body.password, _DUMMY_HASH)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    return TokenOut(
        access_token=create_access_token(user.id),
        refresh_token=sessions.issue(db, user),
    )


_DUMMY_HASH = hash_password("timing-equalizer-dummy")


@router.post(
    "/auth/refresh",
    responses={200: {"model": TokenOut}},
    dependencies=[rate_limit("refresh", 60, 3600)],
)
def refresh_session(body: RefreshIn, db: DbDep):
    try:
        user, new_token = sessions.rotate(db, body.refresh_token)
    except sessions.SessionReplayed:
        # Returned, not raised: the family revocation must commit with this
        # request, and raising would roll it back.
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Session revoked — please sign in again"},
        )
    return TokenOut(access_token=create_access_token(user.id), refresh_token=new_token)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(body: RefreshIn, db: DbDep):
    sessions.revoke(db, body.refresh_token)


@router.post("/me/sessions/revoke-all", status_code=status.HTTP_200_OK)
def revoke_all_sessions(user: CurrentUser, db: DbDep):
    """Sign out everywhere — the control you want after losing a phone."""
    return {"revoked": sessions.revoke_all_for_user(db, user)}


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser):
    return user


@router.put("/me/worker-profile", response_model=WorkerProfileOut)
def upsert_worker_profile(body: WorkerProfileIn, user: CurrentUser, db: DbDep):
    if not user.can_work:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="Account role does not allow working; register as worker or both",
        )
    profile = db.get(WorkerProfile, user.id)
    if profile is None:
        profile = WorkerProfile(user_id=user.id, **body.model_dump())
        db.add(profile)
    else:
        for field, value in body.model_dump().items():
            setattr(profile, field, value)
    db.flush()
    return profile


@router.get("/me/worker-profile", response_model=WorkerProfileOut)
def get_worker_profile(user: CurrentUser, db: DbDep):
    profile = db.get(WorkerProfile, user.id)
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No worker profile")
    return profile


@router.post("/me/worker-profile/submit-vetting", response_model=WorkerProfileOut)
def submit_vetting(user: CurrentUser, db: DbDep):
    profile = db.get(WorkerProfile, user.id)
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No worker profile")
    if profile.vetting_status not in (VettingStatus.UNVERIFIED, VettingStatus.REJECTED):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Vetting already submitted")
    if not user.phone_verified:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="Verify your phone before submitting vetting"
        )
    profile.vetting_status = VettingStatus.PENDING
    db.flush()
    return profile


@router.post(
    "/me/phone/request-verification",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[rate_limit("otp_request", 5, 3600)],
)
def request_phone_verification(body: PhoneRequestIn, user: CurrentUser, db: DbDep):
    otp.request_verification(db, user, body.phone)
    return {"detail": "Verification code sent"}


@router.post("/me/phone/verify", response_model=UserOut)
def verify_phone(body: PhoneVerifyIn, user: CurrentUser, db: DbDep):
    return otp.verify_code(db, user, body.code)


@router.get("/dev/sms-outbox")
def dev_sms_outbox(user: CurrentUser):
    """Dev-only: read SMS codes the fake sender 'delivered'. 404 outside dev."""
    from app.core.config import get_settings
    from app.modules.identity.sms import DevSmsSender

    if get_settings().environment != "dev":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
    return [
        {"phone": phone, "message": message}
        for phone, message in DevSmsSender.outbox[-10:]
        if user.phone is None or phone == user.phone
    ]

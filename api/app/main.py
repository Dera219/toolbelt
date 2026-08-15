import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.db import Base, engine
from app.modules.identity import models as identity_models  # noqa: F401  (register tables)
from app.modules.identity.router import router as identity_router
from app.modules.jobs import models as jobs_models  # noqa: F401  (register tables)
from app.modules.jobs.router import router as jobs_router
from app.modules.chat import models as chat_models  # noqa: F401  (register tables)
from app.modules.chat.router import router as chat_router
from app.modules.notifications.router import router as notifications_router
from app.modules.payments import models as payments_models  # noqa: F401  (register tables)
from app.modules.payments.provider import ProviderError
from app.modules.payments.router import router as payments_router
from app.modules.reputation import models as reputation_models  # noqa: F401  (register tables)
from app.modules.reputation.router import router as reputation_router
from app.modules.trust import models as trust_models  # noqa: F401  (register tables)
from app.modules.trust.router import disputes_router
from app.modules.trust.router import router as trust_router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Dev/test bootstrap only. In prod Alembic owns the schema — it runs before
    # the server starts (see api/Dockerfile). Calling create_all there would
    # create tables outside migration control and can mask a failed migration.
    if get_settings().environment != "prod":
        Base.metadata.create_all(engine)
    from app.modules.identity.oidc import check_signing_support

    check_signing_support()
    yield


settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

if settings.environment != "prod":  # prod serves photos from S3/CDN, not the API
    from pathlib import Path

    from fastapi.staticfiles import StaticFiles

    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    app.mount("/uploads", StaticFiles(directory=settings.upload_dir), name="uploads")

# CORS, in both environments — because the web build is a real client now.
#
# It used to live inside the dev-only block above, which had a consequence
# nobody had needed to face: in prod no CORS middleware was installed at all,
# so a browser on any other origin discarded every response. That was correct
# while the only client was a native app talking to the API directly, and it
# silently forbade the browser client the moment one existed.
#
# Dev allows localhost by regex so `expo start --web` needs no configuration.
# Prod allows exactly the origins named in TOOLBELT_WEB_APP_ORIGINS and nothing
# else — never a wildcard, because `allow_credentials` is on and the two
# together let any page read a signed-in user's data. Unset means no browser
# client, which is the prior behaviour and a safe default rather than an
# accident.
_cors_origins = [o.strip() for o in settings.web_app_origins.split(",") if o.strip()]
if settings.environment != "prod":
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
elif _cors_origins:
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
app.include_router(identity_router, tags=["identity"])
app.include_router(jobs_router, tags=["jobs"])
app.include_router(reputation_router, tags=["reputation"])
app.include_router(chat_router, tags=["chat"])
app.include_router(notifications_router, tags=["notifications"])
app.include_router(payments_router, tags=["payments"])
app.include_router(disputes_router, tags=["disputes"])
app.include_router(trust_router, tags=["admin"])


@app.exception_handler(ProviderError)
async def provider_error_handler(_request: Request, exc: ProviderError):
    """A payment provider failure is an upstream fault, not a crash. Returning a
    real response also keeps CORS headers attached — without this the browser
    reports a bare 500 as an unreachable server."""
    # Log the upstream detail; do not return it. Provider messages embed request
    # ids and connected-account refs that clients have no business seeing.
    logging.getLogger(__name__).warning("payment provider error: %s", exc, exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content={"detail": "Payment provider is unavailable. Please try again."},
    )


@app.get("/health", tags=["ops"])
def health():
    return {"status": "ok", "environment": settings.environment}

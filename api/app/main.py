from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import get_settings
from app.core.db import Base, engine
from app.modules.identity import models as identity_models  # noqa: F401  (register tables)
from app.modules.identity.router import router as identity_router
from app.modules.jobs import models as jobs_models  # noqa: F401  (register tables)
from app.modules.jobs.router import router as jobs_router
from app.modules.chat import models as chat_models  # noqa: F401  (register tables)
from app.modules.chat.router import router as chat_router
from app.modules.payments import models as payments_models  # noqa: F401  (register tables)
from app.modules.payments.router import router as payments_router
from app.modules.reputation import models as reputation_models  # noqa: F401  (register tables)
from app.modules.reputation.router import router as reputation_router
from app.modules.trust.router import router as trust_router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Dev/test bootstrap. Replaced by Alembic migrations when Postgres lands (Phase 2).
    Base.metadata.create_all(engine)
    yield


settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.include_router(identity_router, tags=["identity"])
app.include_router(jobs_router, tags=["jobs"])
app.include_router(reputation_router, tags=["reputation"])
app.include_router(chat_router, tags=["chat"])
app.include_router(payments_router, tags=["payments"])
app.include_router(trust_router, tags=["admin"])


@app.get("/health", tags=["ops"])
def health():
    return {"status": "ok", "environment": settings.environment}

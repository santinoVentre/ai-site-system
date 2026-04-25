"""AI Site System — Agent API main application."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from app.config import get_settings
from app.database import engine
from app.logging_config import configure_logging
from app.models import Base, ContentImage, ContentItem, ContentSection
from app.routers import assets, cms, jobs, projects, qa

settings = get_settings()

configure_logging("agent-api", level=settings.log_level, json_logs=settings.log_json)
logger = logging.getLogger(__name__)


limiter = Limiter(key_func=get_remote_address, default_limits=[settings.rate_limit_default])


async def _run_cms_migrations() -> None:
    """Idempotent runtime migration: ensure CMS tables exist on existing DBs.

    `init-db.sql` already creates them on first install, but pre-existing
    deployments (which were provisioned before the CMS landed) need this safety
    net. Uses SQLAlchemy's `create_all` with `checkfirst=True`.
    """
    try:
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: Base.metadata.create_all(
                    sync_conn,
                    tables=[
                        ContentSection.__table__,
                        ContentItem.__table__,
                        ContentImage.__table__,
                    ],
                    checkfirst=True,
                )
            )
        logger.info("CMS schema verified")
    except Exception as exc:
        logger.exception("CMS schema migration failed: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Agent API starting up")
    await _run_cms_migrations()
    yield
    logger.info("Agent API shutting down")


app = FastAPI(
    title="AI Site System — Agent API",
    version="1.0.0",
    lifespan=lifespan,
)

app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": f"Rate limit exceeded: {exc.detail}"},
    )


app.add_middleware(SlowAPIMiddleware)


_cors_origins = [o.strip() for o in settings.cors_allowed_origins.split(",") if o.strip()]
if not _cors_origins:
    _cors_origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    allow_credentials=True,
)


app.include_router(projects.router)
app.include_router(jobs.router)
app.include_router(qa.router)
app.include_router(assets.router)
app.include_router(cms.router)


@app.get("/health")
@limiter.limit(settings.rate_limit_public)
async def health(request: Request):
    return {"status": "ok", "service": "agent-api"}


@app.get("/")
async def root():
    return {
        "service": "ai-site-system-agent-api",
        "version": "1.0.0",
        "endpoints": [
            "/projects",
            "/jobs",
            "/qa",
            "/assets",
            "/cms/kinds",
            "/projects/{id}/cms/sections",
            "/projects/{id}/cms/data",
            "/health",
        ],
    }

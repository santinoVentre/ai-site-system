"""QA Runner — Playwright-based website testing service."""

import logging
import os
from contextlib import asynccontextmanager
from uuid import UUID
from fastapi import FastAPI, BackgroundTasks, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from qa.runner import run_qa_checks
from qa.logging_config import configure_logging

configure_logging(
    "qa-runner",
    level=os.environ.get("LOG_LEVEL", "INFO"),
    json_logs=os.environ.get("LOG_JSON", "1").lower() in {"1", "true", "yes"},
)
logger = logging.getLogger(__name__)


AGENT_API_SECRET = os.environ.get("AGENT_API_SECRET", "")


async def verify_secret(x_api_secret: str = Header(..., alias="X-API-Secret")):
    if not AGENT_API_SECRET or x_api_secret != AGENT_API_SECRET:
        raise HTTPException(status_code=401, detail="Invalid API secret")
    return True


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("QA Runner starting up")
    if not AGENT_API_SECRET:
        logger.warning("AGENT_API_SECRET is not set — /run endpoints will reject all requests")
    yield
    logger.info("QA Runner shutting down")


app = FastAPI(title="AI Site System — QA Runner", lifespan=lifespan)


class QARequest(BaseModel):
    job_id: str
    revision_id: str
    preview_url: str
    callback_url: str | None = None
    viewports: list[dict] = Field(default_factory=lambda: [
        {"name": "desktop", "width": 1920, "height": 1080},
        {"name": "mobile", "width": 375, "height": 812},
    ])
    run_lighthouse: bool = True
    run_axe: bool = True


class QAResponse(BaseModel):
    status: str
    message: str
    report: dict | None = None


@app.post("/run", response_model=QAResponse, dependencies=[Depends(verify_secret)])
async def run_qa(request: QARequest, background_tasks: BackgroundTasks):
    """Trigger a QA run for a preview URL."""
    background_tasks.add_task(
        run_qa_checks,
        job_id=request.job_id,
        revision_id=request.revision_id,
        preview_url=request.preview_url,
        viewports=request.viewports,
        callback_url=request.callback_url,
        run_lighthouse=request.run_lighthouse,
        run_axe=request.run_axe,
    )
    return QAResponse(status="started", message="QA check queued")


@app.post("/run-sync", response_model=QAResponse, dependencies=[Depends(verify_secret)])
async def run_qa_sync(request: QARequest):
    """Run QA synchronously and return the report."""
    report = await run_qa_checks(
        job_id=request.job_id,
        revision_id=request.revision_id,
        preview_url=request.preview_url,
        viewports=request.viewports,
        callback_url=None,
        run_lighthouse=request.run_lighthouse,
        run_axe=request.run_axe,
    )
    return QAResponse(status="complete", message="QA check done", report=report)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "qa-runner"}

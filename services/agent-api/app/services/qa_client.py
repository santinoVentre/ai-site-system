"""Thin HTTP client that triggers the Playwright QA runner and persists the report."""

from __future__ import annotations

import logging
from uuid import UUID
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import QAReport

logger = logging.getLogger(__name__)
settings = get_settings()


async def run_playwright_qa(
    *,
    db: AsyncSession,
    job_id: UUID,
    revision_id: UUID,
    preview_url: str,
) -> dict | None:
    """Call the qa-runner synchronously and save a QAReport row. Returns the report dict."""
    if not settings.qa_enabled:
        return None

    url = settings.qa_runner_url.rstrip("/") + "/run-sync"
    headers = {"X-API-Secret": settings.agent_api_secret}
    payload = {
        "job_id": str(job_id),
        "revision_id": str(revision_id),
        "preview_url": preview_url,
        "viewports": [
            {"name": "desktop", "width": 1920, "height": 1080},
            {"name": "mobile", "width": 375, "height": 812},
        ],
        "run_lighthouse": True,
        "run_axe": True,
    }

    async with httpx.AsyncClient(timeout=180) as client:
        try:
            r = await client.post(url, json=payload, headers=headers)
            r.raise_for_status()
            report = r.json()
        except Exception as exc:
            logger.warning("qa-runner call failed: %s", exc)
            return None

    # Persist
    qa = QAReport(
        job_id=job_id,
        revision_id=revision_id,
        overall_status=report.get("overall_status", "unknown"),
        desktop_score=report.get("desktop_score"),
        mobile_score=report.get("mobile_score"),
        broken_links=report.get("broken_links") or [],
        console_errors=report.get("console_errors") or [],
        accessibility_issues=report.get("accessibility_issues") or report.get("issues") or [],
        screenshots=report.get("screenshots") or {},
        details=report,
    )
    db.add(qa)
    await db.flush()
    return report

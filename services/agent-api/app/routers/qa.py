"""QA router — trigger QA runs and retrieve reports."""

from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.auth import verify_api_secret
from app.models import QAReport, ProjectRevision
from app.schemas import QARunRequest, QAReportOut

router = APIRouter(prefix="/qa", tags=["qa"])


@router.get("/reports/{revision_id}", response_model=list[QAReportOut])
async def get_qa_reports(
    revision_id: UUID,
    db: AsyncSession = Depends(get_db),
    _auth=Depends(verify_api_secret),
):
    result = await db.execute(
        select(QAReport)
        .where(QAReport.revision_id == revision_id)
        .order_by(QAReport.created_at.desc())
    )
    return result.scalars().all()


@router.post("/reports", response_model=QAReportOut)
async def submit_qa_report(
    revision_id: UUID,
    report: dict,
    db: AsyncSession = Depends(get_db),
    _auth=Depends(verify_api_secret),
):
    """Submit a QA report from the qa-runner service."""
    rev_result = await db.execute(
        select(ProjectRevision).where(ProjectRevision.id == revision_id)
    )
    revision = rev_result.scalar_one_or_none()
    if not revision:
        raise HTTPException(status_code=404, detail="Revision not found")

    qa = QAReport(
        job_id=report.get("job_id"),
        revision_id=revision_id,
        overall_status=report.get("overall_status", "pending"),
        desktop_score=report.get("desktop_score"),
        mobile_score=report.get("mobile_score"),
        broken_links=report.get("broken_links", []),
        console_errors=report.get("console_errors", []),
        accessibility_issues=report.get("accessibility_issues", []),
        screenshots=report.get("screenshots", {}),
        visual_diff=report.get("visual_diff", {}),
        details=report.get("details", {}),
    )
    db.add(qa)
    await db.flush()
    return qa

"""Jobs router — create and manage jobs, trigger workflows."""

import asyncio
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, async_session
from app.auth import verify_api_secret
from app.models import Job, Project, ProjectRevision, ChangeRequest
from app.schemas import (
    JobCreate, JobOut, JobEventOut,
    WebsiteCreateRequest, WebsiteCreateResponse,
    WebsiteModifyRequest, WebsiteModifyResponse,
)
from app.workflows.create_website import run_create_website
from app.workflows.modify_website import run_modify_website
from app.services.job_manager import set_job_error, cancel_job as cancel_job_state

router = APIRouter(prefix="/jobs", tags=["jobs"])

import logging
logger = logging.getLogger(__name__)


async def _run_create_in_background(job_id: UUID):
    """Run website creation in a background task with its own DB session.

    Exceptions are surfaced via set_job_error so the job gets a proper JobEvent
    and a human-readable error_message. The BG task never re-raises.
    """
    async with async_session() as db:
        try:
            await run_create_website(db, job_id)
            await db.commit()
        except Exception as exc:
            await db.rollback()
            logger.exception(f"Background create pipeline failed for job {job_id}")
            try:
                async with async_session() as db2:
                    await set_job_error(db2, job_id, f"Background task crashed: {exc}")
                    await db2.commit()
            except Exception:
                logger.exception(f"Could not even record failure for job {job_id}")


async def _run_modify_in_background(job_id: UUID, change_request_id: UUID):
    """Run website modification in a background task with its own DB session."""
    async with async_session() as db:
        try:
            await run_modify_website(db, job_id, change_request_id)
            await db.commit()
        except Exception as exc:
            await db.rollback()
            logger.exception(f"Background modify pipeline failed for job {job_id}")
            try:
                async with async_session() as db2:
                    await set_job_error(db2, job_id, f"Background task crashed: {exc}")
                    await db2.commit()
            except Exception:
                logger.exception(f"Could not even record failure for job {job_id}")


@router.post("/create", response_model=WebsiteCreateResponse)
async def create_website(
    body: WebsiteCreateRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _auth=Depends(verify_api_secret),
):
    """Create a new website from a brief. Triggers the full creation pipeline."""
    job = Job(
        job_type="create_website",
        status="new",
        brief=body.brief,
        config={
            "project_name": body.project_name,
            "skip_research": body.skip_research,
            "uploaded_assets": [a.model_dump() for a in body.uploaded_assets],
            **body.config,
        },
    )
    db.add(job)
    await db.flush()

    # Run pipeline in background
    background_tasks.add_task(_run_create_in_background, job.id)

    return WebsiteCreateResponse(
        job_id=job.id,
        project_id=job.id,  # Will be updated during pipeline
        project_slug="pending",
        status="new",
        message="Website creation started. You will be notified when preview is ready.",
    )


@router.post("/modify", response_model=WebsiteModifyResponse)
async def modify_website(
    body: WebsiteModifyRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _auth=Depends(verify_api_secret),
):
    """Modify an existing website. Triggers the modification pipeline."""
    # Verify project exists
    proj_result = await db.execute(select(Project).where(Project.id == body.project_id))
    project = proj_result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Determine target revision
    target_revision_id = body.target_revision_id or project.current_revision_id
    if not target_revision_id:
        raise HTTPException(status_code=400, detail="No current revision to modify")

    rev_result = await db.execute(
        select(ProjectRevision).where(ProjectRevision.id == target_revision_id)
    )
    target_rev = rev_result.scalar_one_or_none()
    if not target_rev:
        raise HTTPException(status_code=404, detail="Target revision not found")

    # Create change request
    change_request = ChangeRequest(
        project_id=project.id,
        target_revision_id=target_revision_id,
        request_text=body.change_request,
        status="pending",
    )
    db.add(change_request)
    await db.flush()

    # Create job
    job = Job(
        project_id=project.id,
        job_type="modify_website",
        status="new",
        brief=body.change_request,
        config={"target_revision_id": str(target_revision_id)},
    )
    db.add(job)
    await db.flush()

    change_request.job_id = job.id

    # Run pipeline in background
    background_tasks.add_task(_run_modify_in_background, job.id, change_request.id)

    return WebsiteModifyResponse(
        job_id=job.id,
        project_id=project.id,
        change_request_id=change_request.id,
        status="new",
        message="Modification started. You will be notified when preview is ready.",
    )


@router.post("/{job_id}/retry", response_model=JobOut)
async def retry_job(
    job_id: UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _auth=Depends(verify_api_secret),
):
    """Retry a failed job by resetting its state and re-running the pipeline."""
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in ("failed", "cancelled"):
        raise HTTPException(status_code=400, detail=f"Job is not in a retriable state (current: {job.status})")

    # Reset job state
    job.status = "new"
    job.error_message = None
    job.started_at = None
    job.completed_at = None
    job.result = {}
    await db.flush()

    if job.job_type == "create_website":
        background_tasks.add_task(_run_create_in_background, job.id)
    elif job.job_type == "modify_website":
        from app.models import ChangeRequest
        cr_result = await db.execute(
            select(ChangeRequest).where(ChangeRequest.job_id == job_id)
        )
        change_request = cr_result.scalar_one_or_none()
        if not change_request:
            raise HTTPException(status_code=400, detail="No change request found for this modify job")
        background_tasks.add_task(_run_modify_in_background, job.id, change_request.id)
    else:
        raise HTTPException(status_code=400, detail=f"Retry not supported for job type: {job.job_type}")

    return job


@router.post("/{job_id}/cancel", response_model=JobOut)
async def cancel_job(
    job_id: UUID,
    db: AsyncSession = Depends(get_db),
    _auth=Depends(verify_api_secret),
):
    """Cancel an in-flight job. Allowed from any active pipeline status."""
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    try:
        job = await cancel_job_state(db, job_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return job


@router.get("/{job_id}", response_model=JobOut)
async def get_job(
    job_id: UUID,
    db: AsyncSession = Depends(get_db),
    _auth=Depends(verify_api_secret),
):
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/{job_id}/events", response_model=list[JobEventOut])
async def get_job_events(
    job_id: UUID,
    db: AsyncSession = Depends(get_db),
    _auth=Depends(verify_api_secret),
):
    from app.models import JobEvent
    result = await db.execute(
        select(JobEvent)
        .where(JobEvent.job_id == job_id)
        .order_by(JobEvent.created_at)
    )
    return result.scalars().all()


@router.get("", response_model=list[JobOut])
async def list_jobs(
    status: str | None = None,
    job_type: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    _auth=Depends(verify_api_secret),
):
    query = select(Job).order_by(Job.created_at.desc()).limit(limit)
    if status:
        query = query.where(Job.status == status)
    if job_type:
        query = query.where(Job.job_type == job_type)
    result = await db.execute(query)
    return result.scalars().all()

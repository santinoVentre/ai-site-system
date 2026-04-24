"""Projects router — CRUD for projects and revisions."""

from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.auth import verify_api_secret
from app.models import Project, ProjectRevision, Approval, Deployment
from app.schemas import (
    ProjectCreate, ProjectOut, ProjectListOut,
    RevisionOut, ApprovalRequest, ApprovalOut,
)
from app.services.git_manager import rollback_to_revision, merge_revision, diff_commits

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=ProjectListOut)
async def list_projects(
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    _auth=Depends(verify_api_secret),
):
    result = await db.execute(
        select(Project).order_by(Project.created_at.desc()).offset(skip).limit(limit)
    )
    projects = result.scalars().all()
    count_result = await db.execute(select(func.count(Project.id)))
    total = count_result.scalar_one()
    return ProjectListOut(projects=projects, total=total)


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    _auth=Depends(verify_api_secret),
):
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.get("/{project_id}/revisions", response_model=list[RevisionOut])
async def list_revisions(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    _auth=Depends(verify_api_secret),
):
    result = await db.execute(
        select(ProjectRevision)
        .where(ProjectRevision.project_id == project_id)
        .order_by(ProjectRevision.revision_number.desc())
    )
    return result.scalars().all()


@router.get("/{project_id}/revisions/{base_id}/diff/{head_id}")
async def get_revision_diff(
    project_id: UUID,
    base_id: UUID,
    head_id: UUID,
    db: AsyncSession = Depends(get_db),
    _auth=Depends(verify_api_secret),
):
    """Return a unified git diff between two revisions of the project."""
    proj_result = await db.execute(select(Project).where(Project.id == project_id))
    project = proj_result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    base_result = await db.execute(
        select(ProjectRevision).where(ProjectRevision.id == base_id)
    )
    base_rev = base_result.scalar_one_or_none()
    head_result = await db.execute(
        select(ProjectRevision).where(ProjectRevision.id == head_id)
    )
    head_rev = head_result.scalar_one_or_none()
    if not base_rev or not head_rev:
        raise HTTPException(status_code=404, detail="Revision not found")
    if not base_rev.git_commit_hash or not head_rev.git_commit_hash:
        raise HTTPException(status_code=400, detail="Revisions are missing git commit hashes")

    try:
        diff = diff_commits(project.slug, base_rev.git_commit_hash, head_rev.git_commit_hash)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Diff failed: {exc}")

    return {
        "project_id": str(project_id),
        "base_revision_id": str(base_id),
        "head_revision_id": str(head_id),
        **diff,
    }


@router.post("/{project_id}/approve", response_model=ApprovalOut)
async def approve_revision(
    project_id: UUID,
    body: ApprovalRequest,
    db: AsyncSession = Depends(get_db),
    _auth=Depends(verify_api_secret),
):
    """Approve or reject a revision. If approved, promote to production."""
    # Verify project exists
    proj_result = await db.execute(select(Project).where(Project.id == project_id))
    project = proj_result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Verify revision exists
    rev_result = await db.execute(
        select(ProjectRevision).where(ProjectRevision.id == body.revision_id)
    )
    revision = rev_result.scalar_one_or_none()
    if not revision:
        raise HTTPException(status_code=404, detail="Revision not found")

    from datetime import datetime

    approval = Approval(
        project_id=project_id,
        revision_id=body.revision_id,
        decision=body.decision,
        decided_at=datetime.utcnow(),
        notes=body.notes,
    )
    db.add(approval)

    if body.decision == "approved":
        revision.status = "approved"
        revision.approved_at = datetime.utcnow()

        # Merge revision branch if applicable
        if revision.revision_number > 1:
            try:
                merge_revision(project.slug, revision.revision_number)
            except Exception:
                pass  # May already be on main

        # Promote to production
        project.current_revision_id = revision.id
        project.production_revision_id = revision.id
        revision.status = "live"

        # Mark previous live revisions as superseded
        await db.execute(
            ProjectRevision.__table__.update()
            .where(
                ProjectRevision.project_id == project_id,
                ProjectRevision.status == "live",
                ProjectRevision.id != revision.id,
            )
            .values(status="superseded")
        )

    elif body.decision == "rejected":
        revision.status = "rejected"

    await db.flush()
    return approval


@router.post("/{project_id}/rollback")
async def rollback_project(
    project_id: UUID,
    target_revision_id: UUID,
    db: AsyncSession = Depends(get_db),
    _auth=Depends(verify_api_secret),
):
    """Rollback a project to a previous approved revision."""
    proj_result = await db.execute(select(Project).where(Project.id == project_id))
    project = proj_result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    rev_result = await db.execute(
        select(ProjectRevision).where(ProjectRevision.id == target_revision_id)
    )
    target_rev = rev_result.scalar_one_or_none()
    if not target_rev or target_rev.status not in ("approved", "live", "superseded"):
        raise HTTPException(status_code=400, detail="Target revision is not a valid rollback target")

    # Perform git rollback
    new_hash = rollback_to_revision(project.slug, target_rev.git_commit_hash)

    # Create rollback revision
    max_rev = await db.execute(
        select(func.max(ProjectRevision.revision_number))
        .where(ProjectRevision.project_id == project_id)
    )
    new_rev_num = (max_rev.scalar_one() or 0) + 1

    rollback_rev = ProjectRevision(
        project_id=project_id,
        revision_number=new_rev_num,
        parent_revision_id=project.current_revision_id,
        revision_type="rollback",
        summary=f"Rollback to revision {target_rev.revision_number}",
        git_commit_hash=new_hash,
        source_path=project.git_repo_path,
        status="live",
    )
    db.add(rollback_rev)
    await db.flush()

    project.current_revision_id = rollback_rev.id
    project.production_revision_id = rollback_rev.id

    return {"status": "rolled_back", "new_revision_id": str(rollback_rev.id)}

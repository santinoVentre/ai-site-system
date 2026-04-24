"""Website modification workflow — orchestrates the modify pipeline."""

import logging
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Project, ProjectRevision, Job, Artifact, ChangeRequest,
)
from app.services.job_manager import transition_job, set_job_error
from app.services.git_manager import (
    commit_revision, create_revision_branch, copy_revision_for_preview,
    get_project_files, merge_revision,
)
from app.agents.modifier import analyze_for_modification, apply_modification
from app.agents.copy_agent import run_copy_agent
from app.agents.design_agent import run_design_agent
from app.agents.builder import run_builder
from app.agents.reviewer import run_reviewer
from app.services.image_service import fetch_images_for_copy
from app.services.quality_gate import run_quality_gate
from app.services.qa_client import run_playwright_qa
from app.services.notifier import notify_preview_ready, notify_job_failed
from app.config import get_settings
from app.services.llm_client import LLMInfrastructureError, LLMParseError

logger = logging.getLogger(__name__)
settings = get_settings()


async def run_modify_website(
    db: AsyncSession,
    job_id: UUID,
    change_request_id: UUID,
) -> dict:
    """Execute the full website modification pipeline."""
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one()

    cr_result = await db.execute(
        select(ChangeRequest).where(ChangeRequest.id == change_request_id)
    )
    change_request = cr_result.scalar_one()

    proj_result = await db.execute(
        select(Project).where(Project.id == change_request.project_id)
    )
    project = proj_result.scalar_one()

    # Get the target revision
    rev_result = await db.execute(
        select(ProjectRevision).where(
            ProjectRevision.id == change_request.target_revision_id
        )
    )
    target_revision = rev_result.scalar_one()

    try:
        # 1. Start modification
        job = await transition_job(db, job_id, "modifying", agent="modifier")
        change_request.status = "planning"
        await db.commit()

        # 2. Read current project files
        current_files = get_project_files(project.slug)

        # Get original project spec if available
        spec_result = await db.execute(
            select(Artifact).where(
                Artifact.job_id == job.id,
                Artifact.artifact_type == "project_spec",
            ).limit(1)
        )
        # Try to get spec from any job on this project
        if not spec_result.scalar_one_or_none():
            spec_result = await db.execute(
                select(Artifact)
                .join(Job, Job.id == Artifact.job_id)
                .where(
                    Job.project_id == project.id,
                    Artifact.artifact_type == "project_spec",
                )
                .order_by(Artifact.created_at.desc())
                .limit(1)
            )
        spec_artifact = spec_result.scalar_one_or_none()
        project_spec = spec_artifact.content if spec_artifact else None

        # 3. Analyze and create modification plan
        modification_plan = await analyze_for_modification(
            current_files=current_files,
            change_request=change_request.request_text,
            project_spec=project_spec,
            revision_metadata={
                "revision_number": target_revision.revision_number,
                "summary": target_revision.summary,
            },
        )

        change_request.modification_plan = modification_plan
        change_request.parsed_intent = {
            "summary": modification_plan.get("change_request_summary", ""),
            "risk": modification_plan.get("risk_assessment", "unknown"),
            "requires_rebuild": modification_plan.get("requires_full_rebuild", False),
        }
        change_request.impacted_files = modification_plan.get("affected_files", [])
        change_request.status = "in_progress"

        await _save_artifact(db, job_id, None, "modification_plan", modification_plan)
        await db.commit()

        # 4. Determine next revision number
        max_rev = await db.execute(
            select(ProjectRevision.revision_number)
            .where(ProjectRevision.project_id == project.id)
            .order_by(ProjectRevision.revision_number.desc())
            .limit(1)
        )
        current_max = max_rev.scalar_one_or_none() or 0
        new_rev_number = current_max + 1

        # 5. Create revision branch and apply changes
        create_revision_branch(project.slug, new_rev_number)

        if not modification_plan.get("requires_full_rebuild", False):
            # Targeted modification — keep existing site structure and patch files.
            revision_manifest = await apply_modification(
                current_files=current_files,
                modification_plan=modification_plan,
                change_request=change_request.request_text,
            )
        else:
            # Full rebuild — re-run the creative pipeline (copy → design → build)
            # using the existing spec, so the client gets a genuinely new site when asked.
            logger.warning(
                f"Full rebuild required for project {project.slug}: "
                f"{modification_plan.get('rebuild_reason', 'unspecified')}"
            )

            if not project_spec:
                raise RuntimeError(
                    "Full rebuild requested but no project spec is available — cannot rebuild from scratch."
                )

            # Re-run copy with the change request as additional brief context
            rebuild_brief = (
                f"{project_spec}\n\nUpdated requirements from client:\n"
                f"{change_request.request_text}"
            )
            site_copy = await run_copy_agent(project_spec, research=None)
            await _save_artifact(db, job_id, None, "site_copy", site_copy)

            design_tokens = await run_design_agent(project_spec, research=None)
            await _save_artifact(db, job_id, None, "design_tokens", design_tokens)

            image_map = await fetch_images_for_copy(
                project_slug=project.slug,
                site_copy=site_copy,
                project_spec=project_spec,
            )

            build_manifest = await run_builder(
                project_spec=project_spec,
                site_copy=site_copy,
                design_tokens=design_tokens,
                image_urls=image_map,
                project_slug=project.slug,
            )
            await _save_artifact(db, job_id, None, "build_manifest", build_manifest)

            # Present as a revision manifest so downstream code is unchanged
            rebuilt_files = build_manifest.get("files", [])
            revision_manifest = {
                "changed_files": [
                    {"path": f["path"], "action": "modify", "content": f["content"]}
                    for f in rebuilt_files
                ],
                "new_files": [],
                "deleted_files": [],
                "migration_notes": [
                    f"Full rebuild: {modification_plan.get('rebuild_reason', 'major change requested')}"
                ],
                "summary": f"Full rebuild: {modification_plan.get('rebuild_reason', '')}",
            }

        await _save_artifact(db, job_id, None, "revision_manifest", revision_manifest)

        # 6. Write changed files and commit
        files_to_write = []
        for cf in revision_manifest.get("changed_files", []):
            if cf.get("action") != "delete" and cf.get("content"):
                files_to_write.append({
                    "path": cf["path"],
                    "content": cf["content"],
                })

        commit_hash = commit_revision(
            project.slug,
            new_rev_number,
            f"Modification: {change_request.request_text[:100]}",
            files_to_write,
        )

        # 7. Create new revision record
        new_revision = ProjectRevision(
            project_id=project.id,
            revision_number=new_rev_number,
            parent_revision_id=target_revision.id,
            revision_type="modify",
            summary=revision_manifest.get("summary", ""),
            change_description=change_request.request_text,
            git_commit_hash=commit_hash,
            source_path=str(project.git_repo_path),
            status="draft",
            files_changed=revision_manifest.get("changed_files", []),
            diff_summary={
                "new_files": revision_manifest.get("new_files", []),
                "deleted_files": revision_manifest.get("deleted_files", []),
                "migration_notes": revision_manifest.get("migration_notes", []),
            },
        )
        db.add(new_revision)
        await db.flush()

        change_request.resulting_revision_id = new_revision.id
        job.revision_id = new_revision.id
        await db.commit()

        # 8. Quality gate
        job = await transition_job(db, job_id, "qa", agent="reviewer")
        await db.commit()

        updated_files = get_project_files(project.slug)
        review, final_files = await run_quality_gate(
            initial_files=updated_files,
            project_spec=project_spec or {},
            site_copy={},
            design_tokens={},
            image_map=None,
            project_slug=project.slug,
            revision_number=new_rev_number,
        )
        await _save_artifact(db, job_id, new_revision.id, "review_report", review)

        if final_files and review.get("iterations", 0) > 0:
            commit_hash = commit_revision(
                project.slug, new_rev_number,
                f"Quality pass on revision {new_rev_number}",
                final_files,
            )
            new_revision.git_commit_hash = commit_hash
        await db.commit()

        # 9. Preview deploy
        preview_path = copy_revision_for_preview(project.slug, str(new_revision.id))
        preview_url = f"{settings.preview_base_url}/{project.slug}/preview/{new_revision.id}/"
        new_revision.preview_url = preview_url
        new_revision.status = "preview"
        await db.commit()

        # 9.5 Playwright QA (non-fatal)
        if settings.qa_enabled:
            try:
                qa_report = await run_playwright_qa(
                    db=db,
                    job_id=job_id,
                    revision_id=new_revision.id,
                    preview_url=preview_url,
                )
                if qa_report:
                    logger.info(f"QA Playwright (modify): {qa_report.get('overall_status')}")
            except Exception as e:
                logger.warning(f"Playwright QA failed (non-fatal): {e}")

        job = await transition_job(db, job_id, "preview_ready", agent="deployer")
        await db.commit()

        # 10. Await approval
        job = await transition_job(db, job_id, "awaiting_approval", agent="system")
        change_request.status = "preview"

        job.result = {
            "project_id": str(project.id),
            "project_slug": project.slug,
            "revision_id": str(new_revision.id),
            "revision_number": new_rev_number,
            "preview_url": preview_url,
            "review_quality": review.get("overall_quality", "unknown"),
            "review_score": review.get("score"),
            "changes_summary": revision_manifest.get("summary", ""),
        }
        await db.commit()

        try:
            await notify_preview_ready(
                project_name=project.name,
                project_id=str(project.id),
                revision_id=str(new_revision.id),
                preview_url=preview_url,
                score=review.get("score"),
            )
        except Exception as e:
            logger.warning(f"notify_preview_ready failed: {e}")

        return {
            "project_id": str(project.id),
            "project_slug": project.slug,
            "revision_id": str(new_revision.id),
            "revision_number": new_rev_number,
            "preview_url": preview_url,
            "status": "awaiting_approval",
        }

    except LLMInfrastructureError as e:
        logger.error(f"LLM infrastructure error in modify job {job_id}: {e}")
        change_request.status = "failed"
        await set_job_error(
            db, job_id,
            f"[INFRASTRUCTURE ERROR] {e}\n\n"
            "This requires manual intervention: check the LLM API key, model name, and provider settings.",
            agent="modify_workflow",
        )
        await db.commit()
        try:
            await notify_job_failed(project.name, str(job_id), f"Infrastructure error: {e}")
        except Exception:
            pass
        raise

    except LLMParseError as e:
        logger.error(f"LLM parse error in modify job {job_id}: {e}")
        change_request.status = "failed"
        await set_job_error(
            db, job_id,
            f"[LLM OUTPUT ERROR] {e}\n\n"
            "The model returned unparseable output. You can retry or try with a different model.",
            agent="modify_workflow",
        )
        await db.commit()
        try:
            await notify_job_failed(project.name, str(job_id), f"LLM parse error: {e}")
        except Exception:
            pass
        raise

    except Exception as e:
        logger.exception(f"Modify website failed for job {job_id}")
        change_request.status = "failed"
        await set_job_error(db, job_id, str(e), agent="modify_workflow")
        await db.commit()
        try:
            await notify_job_failed(project.name, str(job_id), str(e))
        except Exception:
            pass
        raise


async def _save_artifact(
    db: AsyncSession,
    job_id: UUID,
    revision_id: UUID | None,
    artifact_type: str,
    content: dict,
) -> Artifact:
    artifact = Artifact(
        job_id=job_id,
        revision_id=revision_id,
        artifact_type=artifact_type,
        content=content,
    )
    db.add(artifact)
    await db.flush()
    return artifact

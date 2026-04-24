"""Website creation workflow — orchestrates the full create pipeline."""

import json
import logging
from uuid import UUID
from slugify import slugify
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import attributes as orm_attributes

from app.models import Project, ProjectRevision, Job, Artifact, ChangeRequest
from app.services.job_manager import transition_job, set_job_error
from app.services.git_manager import (
    init_project_repo, commit_revision, copy_revision_for_preview, get_project_files,
)
from app.agents.planner import run_planner
from app.agents.researcher import run_researcher
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


async def run_create_website(db: AsyncSession, job_id: UUID) -> dict:
    """Execute the full website creation pipeline."""
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one()
    brief = job.brief

    try:
        # 1. Planning
        job = await transition_job(db, job_id, "planning", agent="planner")
        await db.commit()

        project_spec = await run_planner(brief, job.config)
        await _save_artifact(db, job_id, None, "project_spec", project_spec)

        # Create project
        project_name = project_spec.get("project_name", "Untitled")
        slug = slugify(project_name)

        # Ensure unique slug
        existing = await db.execute(select(Project).where(Project.slug == slug))
        if existing.scalar_one_or_none():
            import uuid
            slug = f"{slug}-{str(uuid.uuid4())[:8]}"

        project = Project(
            slug=slug,
            name=project_name,
            description=brief,
            git_repo_path=init_project_repo(slug),
        )
        db.add(project)
        await db.flush()

        job.project_id = project.id
        await db.commit()

        # 2. Research (optional)
        skip_research = job.config.get("skip_research", False)
        research = None
        if not skip_research:
            job = await transition_job(db, job_id, "researching", agent="researcher")
            await db.commit()

            research = await run_researcher(project_spec)
            await _save_artifact(db, job_id, None, "research_report", research)
            await db.commit()

        # 3. Copy
        job = await transition_job(db, job_id, "writing", agent="copy_agent")
        await db.commit()

        site_copy = await run_copy_agent(project_spec, research)
        await _save_artifact(db, job_id, None, "site_copy", site_copy)
        await db.commit()

        # 4. Design
        job = await transition_job(db, job_id, "designing", agent="design_agent")
        await db.commit()

        uploaded_assets = job.config.get("uploaded_assets") or []
        design_tokens = await run_design_agent(project_spec, research, uploaded_assets=uploaded_assets)
        await _save_artifact(db, job_id, None, "design_tokens", design_tokens)
        await db.commit()

        # 4.5 Image fetch — pull real images from Unsplash/Pexels based on copy queries
        image_map = await fetch_images_for_copy(
            project_slug=slug,
            site_copy=site_copy,
            project_spec=project_spec,
        )
        if image_map:
            await _save_artifact(db, job_id, None, "image_map", {"images": image_map})
            await db.commit()

        # ---- Google Sheets setup (BEFORE build so the data URL is baked in) ----
        dynamic_sections = project_spec.get("dynamic_sections", [])
        sheets_enabled = job.config.get("sheets_enabled", False)
        sheets_data_url: str | None = None

        if sheets_enabled or dynamic_sections:
            try:
                from app.services import sheets_service
                sheets_url = job.config.get("sheets_url")
                client_email = (
                    job.config.get("client_email") or job.config.get("sheets_client_email")
                )
                if sheets_url:
                    sheets_info = await sheets_service.connect_spreadsheet(
                        settings.google_sheets_credentials_path, sheets_url
                    )
                    logger.info(f"Connected existing sheet: {sheets_info['sheet_id']}")
                else:
                    sections_to_create = dynamic_sections or job.config.get("sheets_sections", [])
                    sheets_info = await sheets_service.create_spreadsheet(
                        credentials_path=settings.google_sheets_credentials_path,
                        title=project_name,
                        sections=sections_to_create,
                        client_email=client_email,
                    )
                    logger.info(f"Created sheet: {sheets_info['sheet_id']}")

                if not project.metadata_:
                    project.metadata_ = {}
                project.metadata_["sheets"] = {
                    "connected": True,
                    "sheet_id": sheets_info["sheet_id"],
                    "sheet_url": sheets_info["sheet_url"],
                    "sheet_title": sheets_info["sheet_title"],
                    "sections": sheets_info["sections"],
                    "client_email": sheets_info.get("client_email"),
                }
                orm_attributes.flag_modified(project, "metadata_")
                sheets_data_url = (
                    f"{settings.site_base_url}/api/projects/{slug}/sheets/data"
                )
                await db.commit()
            except Exception as e:
                logger.warning(f"Sheets setup failed (non-fatal): {e}")

        # 5. Build — catalog-driven LayoutPlan + Jinja assembly
        job = await transition_job(db, job_id, "building", agent="builder")
        await db.commit()

        build_manifest = await run_builder(
            project_spec=project_spec,
            site_copy=site_copy,
            design_tokens=design_tokens,
            image_urls=image_map,
            project_slug=slug,
            sheets_data_url=sheets_data_url,
        )
        await _save_artifact(db, job_id, None, "build_manifest", build_manifest)

        # Write files and commit
        files = build_manifest.get("files", [])
        commit_hash = commit_revision(slug, 1, f"Initial build: {project_name}", files)

        # Create revision record
        revision = ProjectRevision(
            project_id=project.id,
            revision_number=1,
            revision_type="create",
            summary=f"Initial build from brief",
            git_commit_hash=commit_hash,
            source_path=str(project.git_repo_path),
            status="draft",
            files_changed=[{"path": f["path"], "action": "create"} for f in files],
        )
        db.add(revision)
        await db.flush()

        project.current_revision_id = revision.id
        job.revision_id = revision.id
        await db.commit()

        # 6. Quality gate — reviewer loop (optionally iterates builder)
        job = await transition_job(db, job_id, "qa", agent="reviewer")
        await db.commit()

        project_files = get_project_files(slug)
        review, final_files = await run_quality_gate(
            initial_files=project_files,
            project_spec=project_spec,
            site_copy=site_copy,
            design_tokens=design_tokens,
            image_map=image_map,
            project_slug=slug,
            revision_number=1,
        )
        await _save_artifact(db, job_id, revision.id, "review_report", review)

        # If the quality gate rewrote files, commit them as an updated revision
        if final_files and review.get("iterations", 0) > 0:
            commit_hash = commit_revision(slug, 1, f"Quality pass on revision 1", final_files)
            revision.git_commit_hash = commit_hash
        await db.commit()

        # 7. Preview deploy
        preview_path = copy_revision_for_preview(slug, str(revision.id))
        preview_url = f"{settings.preview_base_url}/{slug}/preview/{revision.id}/"
        revision.preview_url = preview_url
        revision.status = "preview"
        await db.commit()

        # 7.5 Playwright QA (non-fatal — failures degrade but don't block)
        if settings.qa_enabled:
            try:
                qa_report = await run_playwright_qa(
                    db=db,
                    job_id=job_id,
                    revision_id=revision.id,
                    preview_url=preview_url,
                )
                if qa_report:
                    logger.info(
                        f"QA Playwright: {qa_report.get('overall_status')} "
                        f"(desktop={qa_report.get('desktop_score')}, mobile={qa_report.get('mobile_score')})"
                    )
            except Exception as e:
                logger.warning(f"Playwright QA failed (non-fatal): {e}")

        job = await transition_job(db, job_id, "preview_ready", agent="deployer")
        await db.commit()

        # 8. Await approval
        job = await transition_job(db, job_id, "awaiting_approval", agent="system")
        job.result = {
            "project_id": str(project.id),
            "project_slug": slug,
            "revision_id": str(revision.id),
            "preview_url": preview_url,
            "review_quality": review.get("overall_quality", "unknown"),
            "review_score": review.get("score"),
            "review_iterations": review.get("iterations", 0),
        }
        await db.commit()

        # Notify Telegram admin — proactive, non-fatal
        try:
            await notify_preview_ready(
                project_name=project.name,
                project_id=str(project.id),
                revision_id=str(revision.id),
                preview_url=preview_url,
                score=review.get("score"),
            )
        except Exception as e:
            logger.warning(f"notify_preview_ready failed: {e}")

        return {
            "project_id": str(project.id),
            "project_slug": slug,
            "revision_id": str(revision.id),
            "preview_url": preview_url,
            "status": "awaiting_approval",
        }

    except LLMInfrastructureError as e:
        logger.error(f"LLM infrastructure error in create job {job_id}: {e}")
        await set_job_error(
            db, job_id,
            f"[INFRASTRUCTURE ERROR] {e}\n\n"
            "This requires manual intervention: check the LLM API key, model name, and provider settings.",
            agent="create_workflow",
        )
        await db.commit()
        try:
            await notify_job_failed(None, str(job_id), f"Infrastructure error: {e}")
        except Exception:
            pass
        raise

    except LLMParseError as e:
        logger.error(f"LLM parse error in create job {job_id}: {e}")
        await set_job_error(
            db, job_id,
            f"[LLM OUTPUT ERROR] {e}\n\n"
            "The model returned unparseable output. You can retry or try with a different model.",
            agent="create_workflow",
        )
        await db.commit()
        try:
            await notify_job_failed(None, str(job_id), f"LLM parse error: {e}")
        except Exception:
            pass
        raise

    except Exception as e:
        logger.exception(f"Create website failed for job {job_id}")
        await set_job_error(db, job_id, str(e), agent="create_workflow")
        await db.commit()
        try:
            await notify_job_failed(None, str(job_id), str(e))
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
    """Save an agent output as an artifact."""
    artifact = Artifact(
        job_id=job_id,
        revision_id=revision_id,
        artifact_type=artifact_type,
        content=content,
    )
    db.add(artifact)
    await db.flush()
    return artifact

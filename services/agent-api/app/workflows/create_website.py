"""Website creation workflow — orchestrates the full create pipeline."""

import json
import logging
import re
from uuid import UUID
from slugify import slugify
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cms import KIND_REGISTRY, get_kind, validate_item_data, validate_section_settings
from app.models import (
    Artifact,
    ChangeRequest,
    ContentItem,
    ContentSection,
    Job,
    Project,
    ProjectRevision,
)
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

        # ---- CMS setup — auto-create dynamic sections from the planner spec ----
        # The planner emits typed dynamic_sections (kind/key/label). We seed
        # ContentSection rows here and assemble an in-memory cms_data payload
        # so the very first build is prerendered server-side (great for SEO).
        cms_data = await _seed_cms_sections(
            db=db,
            project=project,
            dynamic_sections=project_spec.get("dynamic_sections") or [],
            site_copy=site_copy,
        )
        cms_data_url = (
            f"{settings.site_base_url}/api/projects/{slug}/cms/data"
            if cms_data else None
        )
        await db.commit()

        # 5. Build — catalog-driven LayoutPlan + Jinja assembly
        job = await transition_job(db, job_id, "building", agent="builder")
        await db.commit()

        build_manifest = await run_builder(
            project_spec=project_spec,
            site_copy=site_copy,
            design_tokens=design_tokens,
            image_urls=image_map,
            project_slug=slug,
            cms_data=cms_data,
            cms_data_url=cms_data_url,
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
            cms_data=cms_data,
            cms_data_url=cms_data_url,
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


_CMS_KEY_RE = re.compile(r"[^a-z0-9]+")


def _normalize_cms_key(raw: str, fallback: str) -> str:
    base = _CMS_KEY_RE.sub("-", (raw or "").lower()).strip("-")
    return base or fallback


async def _seed_cms_sections(
    *,
    db: AsyncSession,
    project: Project,
    dynamic_sections: list[dict],
    site_copy: dict,
) -> dict[str, dict]:
    """Create ContentSection rows from the planner spec and return a payload
    ready to be passed to `assemble_site(cms_data=...)`.

    Each entry in `dynamic_sections` is expected in the new typed shape:
        {"kind": "menu", "key": "menu", "label": "Menu", "seed_examples": true}

    Examples are taken from the kind registry. If `site_copy` carries
    section-specific copy (eyebrow / headline / subheadline) we attach it as the
    section settings so the prerendered HTML already has good titles before the
    customer touches the CMS.
    """
    if not dynamic_sections:
        return {}

    payload: dict[str, dict] = {}
    used_keys: set[str] = set()

    copy_sections = (site_copy.get("sections") if isinstance(site_copy, dict) else None) or {}

    for idx, raw in enumerate(dynamic_sections):
        if not isinstance(raw, dict):
            continue
        kind = (raw.get("kind") or "").strip().lower()
        if kind not in KIND_REGISTRY:
            logger.info("Skipping dynamic section with unknown kind: %r", kind)
            continue

        spec = get_kind(kind)
        candidate_key = _normalize_cms_key(raw.get("key") or raw.get("name") or kind, kind)
        key = candidate_key
        n = 2
        while key in used_keys:
            key = f"{candidate_key}-{n}"
            n += 1
        used_keys.add(key)

        label = (raw.get("label") or spec["default_label"]).strip()

        # Pull section-level copy from site_copy if present, then validate.
        section_copy = copy_sections.get(key) or copy_sections.get(kind) or {}
        settings_seed = {
            k: section_copy.get(k) for k in ("eyebrow", "headline", "subheadline")
            if section_copy.get(k)
        }
        try:
            settings = validate_section_settings(kind, settings_seed)
        except Exception:
            settings = {}

        section = ContentSection(
            project_id=project.id,
            kind=kind,
            key=key,
            label=label,
            settings=settings,
            position=idx + 1,
        )
        db.add(section)
        await db.flush()

        items_payload: list[dict] = []
        if raw.get("seed_examples", True):
            for i, example in enumerate(spec.get("examples") or []):
                try:
                    cleaned = validate_item_data(kind, example)
                except ValueError:
                    continue
                db.add(ContentItem(
                    section_id=section.id,
                    position=i + 1,
                    data=cleaned,
                ))
                items_payload.append(cleaned)
            if items_payload:
                await db.flush()

        payload[key] = {
            "kind": kind,
            "label": label,
            "settings": settings,
            "items": items_payload,
        }
        logger.info(
            "Seeded CMS section project=%s kind=%s key=%s items=%d",
            project.slug, kind, key, len(items_payload),
        )

    return payload

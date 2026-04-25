"""CMS publish — re-render the static site whenever the customer edits content.

This is a *deterministic* rebuild: it does NOT call the LLM. We re-run the
catalog-driven assembly step against the latest LayoutPlan + design tokens +
copy, but injecting the freshly edited CMS data so the dynamic sections are
prerendered server-side (great for SEO + first-paint).

Flow:
    1. load Project + latest revision artifacts (build_manifest, design_tokens,
       site_copy, project_spec, image_map, layout_plan)
    2. fetch CMS sections from DB
    3. call assemble_site() with cms_data injected
    4. write the new HTML files into the live preview directory + commit a new
       revision (revision_type = "cms_update")
    5. swap the production_revision_id pointer so the live site is updated
       immediately
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.sections import assemble_site
from app.config import get_settings
from app.models import (
    Artifact,
    ContentItem,
    ContentSection,
    Job,
    Project,
    ProjectRevision,
)
from app.services.git_manager import (
    commit_revision,
    copy_revision_for_preview,
    get_project_files,
)

logger = logging.getLogger(__name__)
settings = get_settings()


async def _latest_artifact(db: AsyncSession, project_id: UUID, artifact_type: str) -> dict | None:
    result = await db.execute(
        select(Artifact)
        .join(Job, Job.id == Artifact.job_id)
        .where(
            Job.project_id == project_id,
            Artifact.artifact_type == artifact_type,
        )
        .order_by(desc(Artifact.created_at))
        .limit(1)
    )
    art = result.scalars().first()
    return art.content if art else None


async def _build_cms_payload(db: AsyncSession, project_id: UUID) -> dict[str, dict]:
    """Return {section_key: {"kind": ..., "items": [...], "settings": {...}}}."""
    sections_q = await db.execute(
        select(ContentSection)
        .where(ContentSection.project_id == project_id)
        .order_by(ContentSection.position, ContentSection.created_at)
    )
    sections = sections_q.scalars().all()
    payload: dict[str, dict] = {}
    for section in sections:
        items_q = await db.execute(
            select(ContentItem)
            .where(ContentItem.section_id == section.id)
            .order_by(ContentItem.position, ContentItem.created_at)
        )
        items = items_q.scalars().all()
        payload[section.key] = {
            "kind": section.kind,
            "label": section.label,
            "settings": section.settings or {},
            "items": [item.data for item in items],
        }
    return payload


async def get_cms_payload(db: AsyncSession, project_id: UUID) -> dict[str, dict]:
    """Public wrapper around `_build_cms_payload` (used by the read endpoint)."""
    return await _build_cms_payload(db, project_id)


async def republish_project(db: AsyncSession, project_id: UUID, *, message: str | None = None) -> dict:
    """Re-assemble the static site with the latest CMS content and deploy as a
    new revision. Returns metadata about the new revision.

    Raises RuntimeError if the project has no buildable history.
    """
    proj_result = await db.execute(select(Project).where(Project.id == project_id))
    project = proj_result.scalar_one_or_none()
    if not project:
        raise RuntimeError("Project not found")

    project_spec = await _latest_artifact(db, project_id, "project_spec") or {}
    site_copy = await _latest_artifact(db, project_id, "site_copy") or {}
    design_tokens = await _latest_artifact(db, project_id, "design_tokens") or {}
    layout_plan = await _latest_artifact(db, project_id, "layout_plan") or {}
    image_map = (await _latest_artifact(db, project_id, "image_map") or {}).get("images") or {}
    build_manifest = await _latest_artifact(db, project_id, "build_manifest") or {}

    if not layout_plan:
        layout_plan = build_manifest.get("layout_plan") or {}

    if not layout_plan:
        raise RuntimeError(
            "Impossibile pubblicare: il progetto non ha ancora una build iniziale "
            "(LayoutPlan mancante)."
        )

    cms_data = await _build_cms_payload(db, project_id)
    cms_data_url = f"{settings.site_base_url}/api/projects/{project.slug}/cms/data"

    site_files_map = assemble_site(
        project_spec=project_spec,
        site_copy=site_copy,
        design_tokens=design_tokens,
        layout_plan=layout_plan,
        image_urls=image_map,
        cms_data=cms_data,
        cms_data_url=cms_data_url,
    )

    files_to_write = []
    for path, content in site_files_map.items():
        files_to_write.append({"path": path, "content": content, "type": _guess_type(path)})

    last_rev_q = await db.execute(
        select(ProjectRevision)
        .where(ProjectRevision.project_id == project_id)
        .order_by(desc(ProjectRevision.revision_number))
    )
    last_rev = last_rev_q.scalars().first()
    next_number = (last_rev.revision_number if last_rev else 0) + 1

    summary = message or f"Aggiornamento contenuti CMS"
    commit_hash = commit_revision(project.slug, next_number, summary, files_to_write)

    revision = ProjectRevision(
        project_id=project_id,
        revision_number=next_number,
        revision_type="cms_update",
        summary=summary,
        change_description="Aggiornamento contenuti dinamici dal CMS",
        git_commit_hash=commit_hash,
        source_path=str(project.git_repo_path or ""),
        files_changed=[{"path": f["path"], "action": "update"} for f in files_to_write],
        status="live",
    )
    db.add(revision)
    await db.flush()

    preview_path = copy_revision_for_preview(project.slug, str(revision.id))
    preview_url = f"{settings.preview_base_url}/{project.slug}/preview/{revision.id}/"
    revision.preview_url = preview_url

    project.current_revision_id = revision.id
    project.production_revision_id = revision.id

    return {
        "revision_id": str(revision.id),
        "revision_number": next_number,
        "preview_url": preview_url,
        "files_changed": len(files_to_write),
    }


def _guess_type(path: str) -> str:
    if "." not in path:
        return "txt"
    ext = path.rsplit(".", 1)[-1].lower()
    return {
        "html": "html",
        "css": "css",
        "js": "js",
        "json": "json",
        "svg": "svg",
        "xml": "xml",
        "txt": "txt",
    }.get(ext, "txt")

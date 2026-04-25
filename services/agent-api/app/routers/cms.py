"""CMS router — typed dynamic content editable from the admin dashboard.

Self-hosted content store the customer can edit autonomously. All
authenticated endpoints require X-API-Secret; the public read endpoint
(`GET /projects/{id}/cms/data`) is open so the generated site can hydrate
dynamic sections at runtime.
"""

from __future__ import annotations

import logging
import re
import uuid as _uuid
from typing import Any
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
)
from slugify import slugify
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import attributes

from app.auth import verify_api_secret
from app.cms import (
    KIND_REGISTRY,
    available_kinds,
    get_kind,
    validate_item_data,
)
from app.cms.kinds import validate_section_settings
from app.database import get_db
from app.models import (
    ContentImage,
    ContentItem,
    ContentSection,
    Project,
)
from app.schemas import (
    ContentImageOut,
    ContentItemCreate,
    ContentItemOut,
    ContentItemUpdate,
    ContentSectionCreate,
    ContentSectionOut,
    ContentSectionUpdate,
    ReorderRequest,
)
from app.services import cms_image_service
from app.services.cms_publish import get_cms_payload, republish_project

logger = logging.getLogger(__name__)


router = APIRouter(tags=["cms"])


# ---- Helpers ----------------------------------------------------------------

async def _get_project(db: AsyncSession, project_id: str) -> Project:
    """Look up by UUID or slug."""
    try:
        uid = _uuid.UUID(project_id)
        result = await db.execute(select(Project).where(Project.id == uid))
    except (ValueError, AttributeError):
        result = await db.execute(select(Project).where(Project.slug == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


async def _get_section(db: AsyncSession, section_id: UUID) -> ContentSection:
    result = await db.execute(
        select(ContentSection).where(ContentSection.id == section_id)
    )
    section = result.scalar_one_or_none()
    if not section:
        raise HTTPException(status_code=404, detail="Sezione non trovata")
    return section


async def _get_item(db: AsyncSession, item_id: UUID) -> ContentItem:
    result = await db.execute(
        select(ContentItem).where(ContentItem.id == item_id)
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Item non trovato")
    return item


async def _next_section_position(db: AsyncSession, project_id: UUID) -> int:
    result = await db.execute(
        select(func.max(ContentSection.position)).where(
            ContentSection.project_id == project_id
        )
    )
    cur = result.scalar_one()
    return (cur or 0) + 1


async def _next_item_position(db: AsyncSession, section_id: UUID) -> int:
    result = await db.execute(
        select(func.max(ContentItem.position)).where(
            ContentItem.section_id == section_id
        )
    )
    cur = result.scalar_one()
    return (cur or 0) + 1


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _make_section_key(label: str, kind: str) -> str:
    base = slugify(label or kind, lowercase=True, max_length=60)
    if not base:
        base = kind
    return base


async def _resolve_section_key(
    db: AsyncSession,
    project_id: UUID,
    candidate: str,
) -> str:
    """Ensure the key is unique within the project."""
    candidate = _SLUG_RE.sub("-", (candidate or "").lower()).strip("-") or "section"
    existing = await db.execute(
        select(ContentSection.key).where(ContentSection.project_id == project_id)
    )
    used = {row for row in existing.scalars().all()}
    if candidate not in used:
        return candidate
    n = 2
    while f"{candidate}-{n}" in used:
        n += 1
    return f"{candidate}-{n}"


async def _section_to_out(
    db: AsyncSession,
    section: ContentSection,
) -> ContentSectionOut:
    count_q = await db.execute(
        select(func.count(ContentItem.id)).where(
            ContentItem.section_id == section.id
        )
    )
    count = count_q.scalar_one() or 0
    return ContentSectionOut(
        id=section.id,
        project_id=section.project_id,
        kind=section.kind,
        key=section.key,
        label=section.label,
        position=section.position,
        settings=section.settings or {},
        created_at=section.created_at,
        updated_at=section.updated_at,
        item_count=count,
    )


# ---- Catalog ----------------------------------------------------------------

@router.get("/cms/kinds", dependencies=[Depends(verify_api_secret)])
async def list_kinds() -> list[dict[str, Any]]:
    """Return the full kinds registry (used by the admin UI to render forms)."""
    detail: list[dict[str, Any]] = []
    for kind in available_kinds():
        spec = get_kind(kind["kind"])
        detail.append({
            **kind,
            "fields": spec["fields"],
            "settings_fields": spec.get("settings_fields", []),
        })
    return detail


# ---- Sections ---------------------------------------------------------------

@router.get(
    "/projects/{project_id}/cms/sections",
    dependencies=[Depends(verify_api_secret)],
)
async def list_sections(project_id: str, db: AsyncSession = Depends(get_db)):
    """Return the project's sections as a flat list (with item counts)."""
    project = await _get_project(db, project_id)
    result = await db.execute(
        select(ContentSection)
        .where(ContentSection.project_id == project.id)
        .order_by(ContentSection.position, ContentSection.created_at)
    )
    sections = result.scalars().all()
    return [
        (await _section_to_out(db, s)).model_dump(mode="json") for s in sections
    ]


@router.post(
    "/projects/{project_id}/cms/sections",
    dependencies=[Depends(verify_api_secret)],
)
async def create_section(
    project_id: str,
    body: ContentSectionCreate,
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project(db, project_id)
    if body.kind not in KIND_REGISTRY:
        raise HTTPException(status_code=400, detail=f"Tipo sconosciuto: {body.kind}")

    spec = get_kind(body.kind)
    label = (body.label or spec["default_label"]).strip()
    if not label:
        raise HTTPException(status_code=400, detail="Etichetta sezione vuota")

    key = await _resolve_section_key(
        db, project.id, body.key or _make_section_key(label, body.kind)
    )
    settings = validate_section_settings(body.kind, body.settings or {})
    position = body.position if body.position is not None else (
        await _next_section_position(db, project.id)
    )

    section = ContentSection(
        project_id=project.id,
        kind=body.kind,
        key=key,
        label=label,
        settings=settings,
        position=position,
    )
    db.add(section)
    await db.flush()

    if body.seed_examples:
        for i, example in enumerate(spec.get("examples") or []):
            try:
                cleaned = validate_item_data(body.kind, example)
            except ValueError:
                continue
            db.add(ContentItem(
                section_id=section.id,
                position=i + 1,
                data=cleaned,
            ))
        if spec.get("examples"):
            await db.flush()

    out = await _section_to_out(db, section)
    return out.model_dump(mode="json")


@router.get(
    "/cms/sections/{section_id}",
    dependencies=[Depends(verify_api_secret)],
)
async def get_section(section_id: UUID, db: AsyncSession = Depends(get_db)):
    """Return just the section metadata; items are fetched separately."""
    section = await _get_section(db, section_id)
    return (await _section_to_out(db, section)).model_dump(mode="json")


async def _update_section_impl(
    section_id: UUID,
    body: ContentSectionUpdate,
    db: AsyncSession,
):
    section = await _get_section(db, section_id)
    if body.label is not None:
        section.label = body.label.strip() or section.label
    if body.position is not None:
        section.position = body.position
    if body.settings is not None:
        section.settings = validate_section_settings(section.kind, body.settings)
        attributes.flag_modified(section, "settings")
    await db.flush()
    return (await _section_to_out(db, section)).model_dump(mode="json")


@router.patch(
    "/cms/sections/{section_id}",
    dependencies=[Depends(verify_api_secret)],
)
async def patch_section(
    section_id: UUID,
    body: ContentSectionUpdate,
    db: AsyncSession = Depends(get_db),
):
    return await _update_section_impl(section_id, body, db)


@router.put(
    "/cms/sections/{section_id}",
    dependencies=[Depends(verify_api_secret)],
)
async def put_section(
    section_id: UUID,
    body: ContentSectionUpdate,
    db: AsyncSession = Depends(get_db),
):
    return await _update_section_impl(section_id, body, db)


@router.delete(
    "/cms/sections/{section_id}",
    dependencies=[Depends(verify_api_secret)],
)
async def delete_section(section_id: UUID, db: AsyncSession = Depends(get_db)):
    section = await _get_section(db, section_id)
    await db.execute(delete(ContentSection).where(ContentSection.id == section.id))
    return {"deleted": True, "section_id": str(section_id)}


@router.post(
    "/projects/{project_id}/cms/sections/reorder",
    dependencies=[Depends(verify_api_secret)],
)
async def reorder_sections(
    project_id: str,
    body: ReorderRequest,
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project(db, project_id)
    for i, sid in enumerate(body.ids):
        result = await db.execute(
            select(ContentSection).where(
                ContentSection.id == sid,
                ContentSection.project_id == project.id,
            )
        )
        section = result.scalar_one_or_none()
        if section:
            section.position = i + 1
    await db.flush()
    return {"reordered": True}


# ---- Items ------------------------------------------------------------------

@router.get(
    "/cms/sections/{section_id}/items",
    dependencies=[Depends(verify_api_secret)],
)
async def list_items(section_id: UUID, db: AsyncSession = Depends(get_db)):
    """Return the section's items as a flat ordered list."""
    await _get_section(db, section_id)
    result = await db.execute(
        select(ContentItem)
        .where(ContentItem.section_id == section_id)
        .order_by(ContentItem.position, ContentItem.created_at)
    )
    items = result.scalars().all()
    return [
        ContentItemOut.model_validate(i, from_attributes=True).model_dump(mode="json")
        for i in items
    ]


@router.post(
    "/cms/sections/{section_id}/items",
    dependencies=[Depends(verify_api_secret)],
)
async def create_item(
    section_id: UUID,
    body: ContentItemCreate,
    db: AsyncSession = Depends(get_db),
):
    section = await _get_section(db, section_id)
    try:
        cleaned = validate_item_data(section.kind, body.data or {})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    position = body.position if body.position is not None else (
        await _next_item_position(db, section.id)
    )
    item = ContentItem(
        section_id=section.id,
        position=position,
        data=cleaned,
    )
    db.add(item)
    await db.flush()
    return ContentItemOut.model_validate(item, from_attributes=True).model_dump(mode="json")


async def _update_item_impl(item_id: UUID, body: ContentItemUpdate, db: AsyncSession):
    item = await _get_item(db, item_id)
    section = await _get_section(db, item.section_id)
    if body.data is not None:
        try:
            cleaned = validate_item_data(section.kind, body.data)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        item.data = cleaned
        attributes.flag_modified(item, "data")
    if body.position is not None:
        item.position = body.position
    await db.flush()
    return ContentItemOut.model_validate(item, from_attributes=True).model_dump(mode="json")


@router.patch(
    "/cms/items/{item_id}",
    dependencies=[Depends(verify_api_secret)],
)
async def patch_item(
    item_id: UUID,
    body: ContentItemUpdate,
    db: AsyncSession = Depends(get_db),
):
    return await _update_item_impl(item_id, body, db)


@router.put(
    "/cms/items/{item_id}",
    dependencies=[Depends(verify_api_secret)],
)
async def put_item(
    item_id: UUID,
    body: ContentItemUpdate,
    db: AsyncSession = Depends(get_db),
):
    return await _update_item_impl(item_id, body, db)


@router.delete(
    "/cms/items/{item_id}",
    dependencies=[Depends(verify_api_secret)],
)
async def delete_item(item_id: UUID, db: AsyncSession = Depends(get_db)):
    item = await _get_item(db, item_id)
    await db.execute(delete(ContentItem).where(ContentItem.id == item.id))
    return {"deleted": True, "item_id": str(item_id)}


@router.post(
    "/cms/sections/{section_id}/items/reorder",
    dependencies=[Depends(verify_api_secret)],
)
async def reorder_items(
    section_id: UUID,
    body: ReorderRequest,
    db: AsyncSession = Depends(get_db),
):
    await _get_section(db, section_id)
    for i, iid in enumerate(body.ids):
        result = await db.execute(
            select(ContentItem).where(
                ContentItem.id == iid,
                ContentItem.section_id == section_id,
            )
        )
        item = result.scalar_one_or_none()
        if item:
            item.position = i + 1
    await db.flush()
    return {"reordered": True}


# ---- Images -----------------------------------------------------------------

@router.post(
    "/projects/{project_id}/cms/images",
    dependencies=[Depends(verify_api_secret)],
)
async def upload_image(
    project_id: str,
    file: UploadFile = File(...),
    alt_text: str = Form(default=""),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project(db, project_id)
    content = await file.read()
    try:
        stored = cms_image_service.store_image(
            project_slug=project.slug,
            content=content,
            content_type=(file.content_type or "").lower(),
            original_filename=file.filename,
        )
    except cms_image_service.CmsImageError as e:
        raise HTTPException(status_code=400, detail=str(e))

    record = ContentImage(
        project_id=project.id,
        original_filename=file.filename,
        stored_filename=stored.absolute_path.name,
        mime_type=stored.mime_type,
        size_bytes=stored.size_bytes,
        width=stored.width,
        height=stored.height,
        url=stored.url,
        alt_text=alt_text or "",
    )
    db.add(record)
    await db.flush()
    logger.info(
        "CMS image uploaded for %s: %s (%d bytes)",
        project.slug,
        stored.relative_path,
        stored.size_bytes,
    )
    return ContentImageOut.model_validate(record, from_attributes=True).model_dump(mode="json")


@router.get(
    "/projects/{project_id}/cms/images",
    dependencies=[Depends(verify_api_secret)],
)
async def list_images(project_id: str, db: AsyncSession = Depends(get_db)):
    project = await _get_project(db, project_id)
    result = await db.execute(
        select(ContentImage)
        .where(ContentImage.project_id == project.id)
        .order_by(ContentImage.created_at.desc())
        .limit(500)
    )
    images = result.scalars().all()
    return {
        "project_id": str(project.id),
        "images": [
            ContentImageOut.model_validate(i, from_attributes=True).model_dump(mode="json")
            for i in images
        ],
    }


@router.delete(
    "/cms/images/{image_id}",
    dependencies=[Depends(verify_api_secret)],
)
async def delete_image(image_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ContentImage).where(ContentImage.id == image_id))
    image = result.scalar_one_or_none()
    if not image:
        raise HTTPException(status_code=404, detail="Immagine non trovata")
    project_q = await db.execute(select(Project).where(Project.id == image.project_id))
    project = project_q.scalar_one_or_none()
    if project:
        rel = f"{project.slug}/{image.stored_filename}"
        cms_image_service.delete_image(rel)
    await db.execute(delete(ContentImage).where(ContentImage.id == image.id))
    return {"deleted": True}


# ---- Public read endpoint ---------------------------------------------------

@router.get("/projects/{project_id}/cms/data")
async def public_cms_data(project_id: str, db: AsyncSession = Depends(get_db)):
    """Public — no auth — consumed by the generated site to hydrate sections."""
    project = await _get_project(db, project_id)
    payload = await get_cms_payload(db, project.id)
    return payload


# ---- Republish (rebuild static site with latest CMS content) ----------------

@router.post(
    "/projects/{project_id}/cms/publish",
    dependencies=[Depends(verify_api_secret)],
)
async def publish_cms(project_id: str, db: AsyncSession = Depends(get_db)):
    project = await _get_project(db, project_id)
    try:
        result = await republish_project(db, project.id)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        logger.exception("CMS publish failed for %s", project.slug)
        raise HTTPException(status_code=500, detail=f"Pubblicazione fallita: {e}")
    return {"status": "published", **result}

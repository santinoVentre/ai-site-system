"""Sheets router — Google Sheets integration endpoints for projects."""

import logging
import uuid as _uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import attributes
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import verify_api_secret
from app.config import get_settings
from app.database import get_db
from app.models import Project
from app.schemas import SheetsConnectRequest, SheetsCreateRequest
from app.services import sheets_service

router = APIRouter(prefix="/projects", tags=["sheets"])
logger = logging.getLogger(__name__)
settings = get_settings()


async def _get_project(db: AsyncSession, project_id: str) -> Project:
    """Look up a project by UUID or slug."""
    try:
        uid = _uuid.UUID(project_id)
        result = await db.execute(select(Project).where(Project.id == uid))
    except (ValueError, AttributeError):
        result = await db.execute(select(Project).where(Project.slug == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


# ---- Status ----

@router.get("/{project_id}/sheets/status", dependencies=[Depends(verify_api_secret)])
async def sheets_status(project_id: str, db: AsyncSession = Depends(get_db)):
    """Return current Sheets configuration for a project."""
    project = await _get_project(db, project_id)
    cfg = (project.metadata_ or {}).get("sheets")
    if not cfg:
        return {"connected": False}
    return {
        "connected": True,
        "sheet_id": cfg.get("sheet_id"),
        "sheet_url": cfg.get("sheet_url"),
        "sheet_title": cfg.get("sheet_title"),
        "sections": cfg.get("sections", []),
        "client_email": cfg.get("client_email"),
    }


# ---- Connect existing sheet ----

@router.post("/{project_id}/sheets/connect", dependencies=[Depends(verify_api_secret)])
async def connect_sheets(
    project_id: str,
    body: SheetsConnectRequest,
    db: AsyncSession = Depends(get_db),
):
    """Connect an existing Google Spreadsheet to a project."""
    project = await _get_project(db, project_id)
    creds = settings.google_sheets_credentials_path

    try:
        info = await sheets_service.connect_spreadsheet(creds, body.sheet_url)
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Cannot connect spreadsheet: {e}")

    if body.client_email:
        try:
            await sheets_service.share_with_email(creds, info["sheet_id"], body.client_email)
            info["client_email"] = body.client_email
        except Exception as e:
            logger.warning(f"Could not share sheet: {e}")

    _update_metadata(project, "sheets", info)
    await db.commit()
    return info


# ---- Create new sheet ----

@router.post("/{project_id}/sheets/create", dependencies=[Depends(verify_api_secret)])
async def create_sheets(
    project_id: str,
    body: SheetsCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create a new Google Spreadsheet for a project with dynamic sections."""
    project = await _get_project(db, project_id)
    creds = settings.google_sheets_credentials_path
    title = body.title or f"{project.name} — Dati Sito"

    try:
        info = await sheets_service.create_spreadsheet(
            creds, title, body.sections, body.client_email,
            drive_folder_id=settings.google_drive_folder_id or None,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        # Detect Google API 403 PERMISSION_DENIED (API not enabled)
        err_str = str(e)
        if "403" in err_str or "PERMISSION_DENIED" in err_str:
            raise HTTPException(
                status_code=400,
                detail=f"Google API 403 PERMISSION_DENIED: {err_str}",
            )
        raise HTTPException(status_code=500, detail=f"Could not create spreadsheet: {err_str}")

    _update_metadata(project, "sheets", info)
    await db.commit()
    return info


# ---- Share ----

@router.post("/{project_id}/sheets/share", dependencies=[Depends(verify_api_secret)])
async def share_sheets(
    project_id: str,
    email: str,
    db: AsyncSession = Depends(get_db),
):
    """Share the connected spreadsheet with an email address."""
    project = await _get_project(db, project_id)
    cfg = (project.metadata_ or {}).get("sheets")
    if not cfg or not cfg.get("sheet_id"):
        raise HTTPException(status_code=404, detail="No spreadsheet connected to this project")
    try:
        await sheets_service.share_with_email(
            settings.google_sheets_credentials_path, cfg["sheet_id"], email
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not share: {e}")
    # Store email in config
    cfg["client_email"] = email
    _update_metadata(project, "sheets", cfg)
    await db.commit()
    return {"shared": True, "email": email}


# ---- Disconnect ----

@router.delete("/{project_id}/sheets/disconnect", dependencies=[Depends(verify_api_secret)])
async def disconnect_sheets(project_id: str, db: AsyncSession = Depends(get_db)):
    """Remove Sheets connection from a project."""
    project = await _get_project(db, project_id)
    meta = dict(project.metadata_ or {})
    meta.pop("sheets", None)
    project.metadata_ = meta
    attributes.flag_modified(project, "metadata_")
    await db.commit()
    return {"disconnected": True}


# ---- Public data endpoint (no auth — called by generated site JS) ----

@router.get("/{project_id}/sheets/data")
async def get_sheets_data(project_id: str, db: AsyncSession = Depends(get_db)):
    """Return live data from the connected spreadsheet. Public, no auth required."""
    project = await _get_project(db, project_id)
    cfg = (project.metadata_ or {}).get("sheets")
    if not cfg or not cfg.get("sheet_id"):
        return {}
    try:
        return await sheets_service.fetch_data(
            settings.google_sheets_credentials_path, cfg["sheet_id"]
        )
    except Exception as e:
        logger.warning(f"Could not fetch sheets data for {project_id}: {e}")
        return {}


# ---- Helpers ----

def _update_metadata(project: Project, key: str, value) -> None:
    meta = dict(project.metadata_ or {})
    meta[key] = value
    project.metadata_ = meta
    attributes.flag_modified(project, "metadata_")

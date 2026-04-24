"""Assets router — handles file uploads (logos, design references)."""

import uuid
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse

from app.auth import verify_api_secret
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/assets", tags=["assets"])

ALLOWED_MIME_TYPES = {
    "image/jpeg", "image/jpg", "image/png", "image/gif",
    "image/webp", "image/svg+xml",
}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB


def _uploads_dir() -> Path:
    path = Path(settings.artifacts_path) / "uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path


@router.post("/upload")
async def upload_asset(
    file: UploadFile = File(...),
    asset_type: str = Form(default="reference"),
    description: str = Form(default=""),
    _auth=Depends(verify_api_secret),
):
    """Upload an image asset (logo, design reference, etc.) for use in website creation."""
    if file.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type: {file.content_type}. Allowed: JPEG, PNG, GIF, WEBP, SVG.",
        )

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large. Maximum size is 10 MB.")

    asset_id = str(uuid.uuid4())
    suffix = Path(file.filename or "upload").suffix or ".jpg"
    stored_filename = f"{asset_id}{suffix}"
    dest = _uploads_dir() / stored_filename
    dest.write_bytes(content)

    logger.info(f"Asset uploaded: {stored_filename} ({file.content_type}, {len(content)} bytes)")

    return {
        "asset_id": asset_id,
        "filename": file.filename,
        "stored_filename": stored_filename,
        "content_type": file.content_type,
        "size_bytes": len(content),
        "asset_type": asset_type,
        "description": description,
        "path": str(dest),
    }


@router.get("/{asset_id}")
async def get_asset(
    asset_id: str,
    _auth=Depends(verify_api_secret),
):
    """Retrieve a previously uploaded asset."""
    uploads = _uploads_dir()
    matches = list(uploads.glob(f"{asset_id}.*"))
    if not matches:
        raise HTTPException(status_code=404, detail="Asset not found")
    return FileResponse(matches[0])

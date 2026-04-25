"""CMS image storage — saves, validates, and optimises images uploaded by the
customer through the dashboard.

Storage layout:
    <cms_assets_path>/<project_slug>/<uuid>.<ext>

Public URL:
    <cms_assets_url_prefix>/<project_slug>/<uuid>.<ext>

Images larger than `cms_image_max_dimension` are downscaled keeping aspect
ratio. JPEG/PNG inputs are re-encoded as WebP for size; SVG is stored as-is.
"""

from __future__ import annotations

import io
import logging
import uuid as _uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PIL import Image, ImageOps, UnidentifiedImageError

from app.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()


ALLOWED_MIME_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/svg+xml",
}

MIME_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
}


class CmsImageError(ValueError):
    """User-facing error (415, 413, etc.). Surfaced verbatim to the dashboard."""


@dataclass
class StoredImage:
    relative_path: str          # "<slug>/<uuid>.webp"
    absolute_path: Path         # full path on disk
    url: str                    # public URL ("/cms-assets/<slug>/<uuid>.webp")
    mime_type: str
    size_bytes: int
    width: Optional[int]
    height: Optional[int]


def _project_dir(project_slug: str) -> Path:
    safe_slug = "".join(c for c in project_slug if c.isalnum() or c in "-_")
    if not safe_slug:
        safe_slug = "default"
    path = Path(settings.cms_assets_path) / safe_slug
    path.mkdir(parents=True, exist_ok=True)
    return path


def _public_url(relative: str) -> str:
    prefix = settings.cms_assets_url_prefix.rstrip("/")
    rel = relative.lstrip("/")
    return f"{prefix}/{rel}"


def store_image(
    *,
    project_slug: str,
    content: bytes,
    content_type: str,
    original_filename: str | None = None,
) -> StoredImage:
    """Validate + optimise + persist an uploaded image. Returns metadata."""
    if not content:
        raise CmsImageError("File vuoto.")

    if content_type not in ALLOWED_MIME_TYPES:
        raise CmsImageError(
            f"Formato non supportato: {content_type}. "
            "Sono ammessi JPEG, PNG, GIF, WebP, SVG."
        )

    if len(content) > settings.cms_max_upload_bytes:
        max_mb = settings.cms_max_upload_bytes // (1024 * 1024)
        raise CmsImageError(f"File troppo grande. Massimo {max_mb} MB.")

    project_dir = _project_dir(project_slug)
    image_id = _uuid.uuid4().hex

    if content_type == "image/svg+xml":
        ext = ".svg"
        dest = project_dir / f"{image_id}{ext}"
        dest.write_bytes(content)
        return StoredImage(
            relative_path=f"{project_dir.name}/{dest.name}",
            absolute_path=dest,
            url=_public_url(f"{project_dir.name}/{dest.name}"),
            mime_type="image/svg+xml",
            size_bytes=len(content),
            width=None,
            height=None,
        )

    try:
        with Image.open(io.BytesIO(content)) as img:
            img = ImageOps.exif_transpose(img)
            img.load()
            width, height = img.size

            max_dim = settings.cms_image_max_dimension
            if width > max_dim or height > max_dim:
                img.thumbnail((max_dim, max_dim), Image.LANCZOS)
                width, height = img.size

            if img.mode in ("P", "LA"):
                img = img.convert("RGBA")
            elif img.mode == "CMYK":
                img = img.convert("RGB")

            ext = ".webp"
            mime_out = "image/webp"
            buf = io.BytesIO()
            save_kwargs: dict = {"quality": 85, "method": 6}
            if img.mode == "RGBA":
                save_kwargs["lossless"] = False
            else:
                if img.mode != "RGB":
                    img = img.convert("RGB")
            img.save(buf, format="WEBP", **save_kwargs)
            optimised = buf.getvalue()
    except UnidentifiedImageError as exc:
        raise CmsImageError(f"Impossibile decodificare l'immagine: {exc}") from exc
    except Exception as exc:
        logger.exception("Image processing failed")
        raise CmsImageError(f"Errore di elaborazione immagine: {exc}") from exc

    dest = project_dir / f"{image_id}{ext}"
    dest.write_bytes(optimised)

    return StoredImage(
        relative_path=f"{project_dir.name}/{dest.name}",
        absolute_path=dest,
        url=_public_url(f"{project_dir.name}/{dest.name}"),
        mime_type=mime_out,
        size_bytes=len(optimised),
        width=width,
        height=height,
    )


def delete_image(relative_path: str) -> bool:
    """Remove an image file from disk. Safe against path traversal."""
    if not relative_path:
        return False
    parts = Path(relative_path).parts
    if any(p in ("..", "") for p in parts):
        return False
    path = Path(settings.cms_assets_path).joinpath(*parts).resolve()
    root = Path(settings.cms_assets_path).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return False
    if path.exists() and path.is_file():
        path.unlink()
        return True
    return False

"""Image service — fetches real images from Unsplash/Pexels based on copy image_queries.

Full Unsplash/Pexels/AI gen pipeline lives here. Returns a mapping of
image_query (or section_id) -> final URL served from the generated-sites assets.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import mimetypes
import os
from pathlib import Path
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


GENERATED_ROOT = Path(os.environ.get("GENERATED_SITES_DIR", "/app/data/generated-sites"))


def _asset_dir(project_slug: str) -> Path:
    d = GENERATED_ROOT / project_slug / "assets" / "images"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _collect_image_queries(site_copy: dict, project_spec: dict) -> dict[str, str]:
    """Return a dict of image_key -> english query string."""
    queries: dict[str, str] = {}

    for sid, section in (site_copy.get("sections") or {}).items():
        if not isinstance(section, dict):
            continue
        q = section.get("image_query")
        if q:
            queries[sid] = q
        for item in (section.get("items") or []):
            if not isinstance(item, dict):
                continue
            iq = item.get("image_query") or item.get("id")
            if iq and item.get("image_query"):
                queries[iq] = item["image_query"]

    if not queries and project_spec.get("project_name"):
        queries["hero"] = project_spec["project_name"]
    return queries


async def _unsplash_search(client: httpx.AsyncClient, query: str, access_key: str) -> str | None:
    try:
        r = await client.get(
            "https://api.unsplash.com/search/photos",
            params={"query": query, "per_page": 1, "orientation": "landscape"},
            headers={"Authorization": f"Client-ID {access_key}"},
            timeout=15,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        results = data.get("results") or []
        if not results:
            return None
        return results[0]["urls"]["regular"]
    except Exception as exc:
        logger.info("Unsplash search failed for %r: %s", query, exc)
        return None


async def _replicate_generate(client: httpx.AsyncClient, prompt: str, token: str) -> str | None:
    """Generate an image via Replicate FLUX schnell. Polls for completion (up to ~25s)."""
    try:
        r = await client.post(
            "https://api.replicate.com/v1/models/black-forest-labs/flux-schnell/predictions",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Prefer": "wait=20",
            },
            json={
                "input": {
                    "prompt": prompt,
                    "aspect_ratio": "16:9",
                    "output_format": "jpg",
                    "output_quality": 85,
                }
            },
            timeout=30,
        )
        if r.status_code >= 400:
            logger.info("Replicate generate failed for %r: %s", prompt, r.status_code)
            return None
        data = r.json()
        # Sync-wait response includes "output"; fallback poll loop
        output = data.get("output")
        if isinstance(output, list) and output:
            return output[0]
        if isinstance(output, str):
            return output
        get_url = (data.get("urls") or {}).get("get")
        if not get_url:
            return None
        import asyncio as _asyncio
        for _ in range(15):
            await _asyncio.sleep(1)
            g = await client.get(get_url, headers={"Authorization": f"Bearer {token}"}, timeout=10)
            if g.status_code != 200:
                continue
            gdata = g.json()
            if gdata.get("status") == "succeeded":
                out = gdata.get("output")
                if isinstance(out, list) and out:
                    return out[0]
                if isinstance(out, str):
                    return out
            if gdata.get("status") in {"failed", "canceled"}:
                return None
        return None
    except Exception as exc:
        logger.info("Replicate request failed for %r: %s", prompt, exc)
        return None


async def _pexels_search(client: httpx.AsyncClient, query: str, api_key: str) -> str | None:
    try:
        r = await client.get(
            "https://api.pexels.com/v1/search",
            params={"query": query, "per_page": 1, "orientation": "landscape"},
            headers={"Authorization": api_key},
            timeout=15,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        photos = data.get("photos") or []
        if not photos:
            return None
        return photos[0]["src"]["large"] or photos[0]["src"].get("original")
    except Exception as exc:
        logger.info("Pexels search failed for %r: %s", query, exc)
        return None


async def _download(client: httpx.AsyncClient, url: str, target_dir: Path, key: str) -> str | None:
    try:
        r = await client.get(url, timeout=30, follow_redirects=True)
        if r.status_code != 200:
            return None
        content = r.content
        ctype = r.headers.get("content-type", "")
        ext = mimetypes.guess_extension(ctype.split(";")[0].strip()) or ".jpg"
        if ext == ".jpe":
            ext = ".jpg"
        fname = hashlib.sha1(f"{key}:{url}".encode()).hexdigest()[:16] + ext
        (target_dir / fname).write_bytes(content)
        return f"./assets/images/{fname}"
    except Exception as exc:
        logger.info("Image download failed for %r: %s", url, exc)
        return None


async def fetch_images_for_copy(
    project_slug: str,
    site_copy: dict,
    project_spec: dict,
) -> dict[str, str]:
    """Resolve image_queries to concrete URLs (local paths under assets/images).

    Returns empty dict when no API keys are configured — the templates will
    fall back to gradient placeholders gracefully.
    """
    queries = _collect_image_queries(site_copy, project_spec)
    if not queries:
        return {}

    unsplash_key = (settings.unsplash_access_key or "").strip()
    pexels_key = (settings.pexels_api_key or "").strip()
    replicate_token = (settings.replicate_api_token or "").strip()
    ai_enabled = bool(settings.ai_images_enabled and replicate_token)

    if not unsplash_key and not pexels_key and not ai_enabled:
        logger.info("No image providers configured — skipping image fetch")
        return {}

    target_dir = _asset_dir(project_slug)
    resolved: dict[str, str] = {}

    async with httpx.AsyncClient() as client:
        for key, query in queries.items():
            url = None
            if unsplash_key:
                url = await _unsplash_search(client, query, unsplash_key)
            if not url and pexels_key:
                url = await _pexels_search(client, query, pexels_key)
            if not url and ai_enabled:
                url = await _replicate_generate(client, query, replicate_token)
            if not url:
                continue
            local_path = await _download(client, url, target_dir, key)
            if local_path:
                resolved[key] = local_path
            # Polite rate limit
            await asyncio.sleep(0.2)

    logger.info("ImageService: resolved %d/%d images for %s", len(resolved), len(queries), project_slug)
    return resolved

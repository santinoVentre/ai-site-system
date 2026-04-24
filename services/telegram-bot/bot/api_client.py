"""HTTP client for communicating with the Agent API."""

import logging
from typing import Optional
import httpx
from bot.config import get_bot_settings

logger = logging.getLogger(__name__)
settings = get_bot_settings()


async def api_request(
    method: str,
    path: str,
    json_data: dict | None = None,
    params: dict | None = None,
) -> dict:
    """Make an authenticated request to the agent API."""
    url = f"{settings.agent_api_url}{path}"
    headers = {"X-API-Secret": settings.agent_api_secret}

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.request(
            method=method,
            url=url,
            json=json_data,
            params=params,
            headers=headers,
        )
        response.raise_for_status()
        return response.json()


async def upload_asset(
    file_bytes: bytes,
    filename: str,
    content_type: str,
    asset_type: str = "reference",
    description: str = "",
) -> dict:
    """Upload an image asset to the agent API. Returns the asset metadata dict."""
    url = f"{settings.agent_api_url}/assets/upload"
    headers = {"X-API-Secret": settings.agent_api_secret}

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            url,
            headers=headers,
            data={"asset_type": asset_type, "description": description},
            files={"file": (filename, file_bytes, content_type)},
        )
        response.raise_for_status()
        return response.json()


async def create_website(
    brief: str,
    project_name: str | None = None,
    uploaded_assets: list[dict] | None = None,
) -> dict:
    return await api_request("POST", "/jobs/create", json_data={
        "brief": brief,
        "project_name": project_name,
        "uploaded_assets": uploaded_assets or [],
    })


async def modify_website(project_id: str, change_request: str) -> dict:
    return await api_request("POST", "/jobs/modify", json_data={
        "project_id": project_id,
        "change_request": change_request,
    })


async def get_job_status(job_id: str) -> dict:
    return await api_request("GET", f"/jobs/{job_id}")


async def list_projects() -> dict:
    return await api_request("GET", "/projects")


async def approve_revision(project_id: str, revision_id: str, decision: str, notes: str | None = None) -> dict:
    return await api_request("POST", f"/projects/{project_id}/approve", json_data={
        "revision_id": revision_id,
        "decision": decision,
        "notes": notes,
    })


async def get_project(project_id: str) -> dict:
    return await api_request("GET", f"/projects/{project_id}")


async def list_revisions(project_id: str) -> list:
    return await api_request("GET", f"/projects/{project_id}/revisions")

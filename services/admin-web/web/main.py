"""Admin Dashboard — FastAPI web app with Jinja2 templates."""

import logging
import secrets
import urllib.parse
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends, Form, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from typing import List
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import httpx
from pydantic_settings import BaseSettings
from functools import lru_cache

from web.logging_config import configure_logging


class Settings(BaseSettings):
    database_url: str = ""
    agent_api_url: str = "http://agent-api:8000"
    agent_api_secret: str = ""
    admin_username: str = "admin"
    admin_password: str = "admin"
    admin_secret_key: str = secrets.token_hex(32)
    log_level: str = "INFO"
    log_json: bool = True
    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings():
    return Settings()


settings = get_settings()

configure_logging("admin-web", level=settings.log_level, json_logs=settings.log_json)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Admin dashboard starting")
    yield
    logger.info("Admin dashboard stopping")


app = FastAPI(title="AI Site System — Admin Dashboard", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings.admin_secret_key)

templates = Jinja2Templates(directory="web/templates")


# ---- Auth Helpers ----

class LoginRequired(Exception):
    pass


def require_login(request: Request):
    if not request.session.get("authenticated"):
        raise LoginRequired()


@app.exception_handler(LoginRequired)
async def login_redirect_handler(request: Request, exc: LoginRequired):
    return RedirectResponse(url="/login", status_code=302)


async def api_request(method: str, path: str, **kwargs) -> dict | list:
    url = f"{settings.agent_api_url}{path}"
    headers = {"X-API-Secret": settings.agent_api_secret}
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.request(method, url, headers=headers, **kwargs)
        if resp.is_error:
            detail = str(resp.status_code)
            try:
                body = resp.json()
                detail = body.get("detail") or body.get("message") or str(body)
            except Exception:
                detail = resp.text[:300] or str(resp.status_code)
            raise RuntimeError(f"{detail}")
        return resp.json()


def _redirect_with_error(path: str, message: str) -> RedirectResponse:
    msg = urllib.parse.quote(str(message)[:500])
    sep = "&" if "?" in path else "?"
    return RedirectResponse(url=f"{path}{sep}error={msg}", status_code=302)


def _redirect_with_flash(path: str, key: str, message: str) -> RedirectResponse:
    msg = urllib.parse.quote(str(message)[:500])
    sep = "&" if "?" in path else "?"
    return RedirectResponse(url=f"{path}{sep}{key}={msg}", status_code=302)


# ---- Routes ----

@app.get("/health")
async def health():
    return {"status": "ok", "service": "admin-web"}


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    if username == settings.admin_username and password == settings.admin_password:
        request.session["authenticated"] = True
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse("login.html", {
        "request": request, "error": "Credenziali non valide"
    })


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    require_login(request)
    try:
        projects = await api_request("GET", "/projects")
        jobs = await api_request("GET", "/jobs", params={"limit": "20"})
    except Exception as e:
        projects = {"projects": [], "total": 0}
        jobs = []
        logger.error(f"API error: {e}")

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "projects": projects.get("projects", []),
        "total_projects": projects.get("total", 0),
        "jobs": jobs if isinstance(jobs, list) else [],
        "error": request.query_params.get("error", ""),
        "flash": request.query_params.get("flash", ""),
    })


@app.get("/projects/{project_id}", response_class=HTMLResponse)
async def project_detail(request: Request, project_id: str):
    require_login(request)
    try:
        project = await api_request("GET", f"/projects/{project_id}")
        revisions = await api_request("GET", f"/projects/{project_id}/revisions")
    except Exception as e:
        raise HTTPException(status_code=404, detail="Project not found")

    cms_sections: list = []
    try:
        cms_sections = await api_request("GET", f"/projects/{project_id}/cms/sections")
        if not isinstance(cms_sections, list):
            cms_sections = []
    except Exception:
        cms_sections = []

    qa_reports: list[dict] = []
    current_rev_id = project.get("current_revision_id") if isinstance(project, dict) else None
    if current_rev_id:
        try:
            qa_reports = await api_request("GET", f"/qa/reports/{current_rev_id}")
            if not isinstance(qa_reports, list):
                qa_reports = []
        except Exception:
            qa_reports = []

    return templates.TemplateResponse("project.html", {
        "request": request,
        "project": project,
        "revisions": revisions if isinstance(revisions, list) else [],
        "cms_sections": cms_sections,
        "qa_reports": qa_reports,
        "error": request.query_params.get("error", ""),
        "flash": request.query_params.get("flash", ""),
    })


@app.get("/projects/{project_id}/cms", response_class=HTMLResponse)
async def cms_index(request: Request, project_id: str):
    require_login(request)
    try:
        project = await api_request("GET", f"/projects/{project_id}")
    except Exception:
        raise HTTPException(status_code=404, detail="Project not found")
    return templates.TemplateResponse("cms_index.html", {
        "request": request,
        "project": project,
    })


@app.get("/projects/{project_id}/cms/sections/{section_id}", response_class=HTMLResponse)
async def cms_section_editor(request: Request, project_id: str, section_id: str):
    require_login(request)
    try:
        project = await api_request("GET", f"/projects/{project_id}")
    except Exception:
        raise HTTPException(status_code=404, detail="Project not found")
    return templates.TemplateResponse("cms_editor.html", {
        "request": request,
        "project": project,
        "section_id": section_id,
    })


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_detail(request: Request, job_id: str):
    require_login(request)
    try:
        job = await api_request("GET", f"/jobs/{job_id}")
        events = await api_request("GET", f"/jobs/{job_id}/events")
    except Exception as e:
        raise HTTPException(status_code=404, detail="Job not found")

    # Try to fetch a QA report linked to the job's revision, if any
    qa_reports: list[dict] = []
    rev_id = job.get("revision_id") if isinstance(job, dict) else None
    if rev_id:
        try:
            qa_reports = await api_request("GET", f"/qa/reports/{rev_id}")
            if not isinstance(qa_reports, list):
                qa_reports = []
        except Exception:
            qa_reports = []

    return templates.TemplateResponse("job.html", {
        "request": request,
        "job": job,
        "events": events if isinstance(events, list) else [],
        "qa_reports": qa_reports,
        "error": request.query_params.get("error", ""),
        "flash": request.query_params.get("flash", ""),
    })


@app.post("/create-website")
async def create_website(
    request: Request,
    brief: str = Form(...),
    files: List[UploadFile] = File(default=[]),
    asset_types: List[str] = Form(default=[]),
    descriptions: List[str] = Form(default=[]),
    ai_images: str = Form(default=""),
):
    require_login(request)
    try:
        uploaded_assets = []
        for i, file in enumerate(files):
            if not file.filename:
                continue
            content = await file.read()
            atype = asset_types[i] if i < len(asset_types) else "reference"
            desc = descriptions[i] if i < len(descriptions) else ""
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    f"{settings.agent_api_url}/assets/upload",
                    headers={"X-API-Secret": settings.agent_api_secret},
                    data={"asset_type": atype, "description": desc},
                    files={"file": (file.filename, content, file.content_type or "image/jpeg")},
                )
                resp.raise_for_status()
                uploaded_assets.append(resp.json())

        config_payload: dict = {}
        if ai_images == "1":
            config_payload["ai_images"] = True

        result = await api_request("POST", "/jobs/create", json={
            "brief": brief,
            "uploaded_assets": uploaded_assets,
            "config": config_payload,
        })
        return RedirectResponse(url=f"/jobs/{result['job_id']}", status_code=302)
    except Exception as e:
        logger.error(f"Create website error: {e}")
        return _redirect_with_error("/", f"Creazione fallita: {e}")


@app.post("/modify-website")
async def modify_website(
    request: Request,
    project_id: str = Form(...),
    change_request: str = Form(...),
):
    require_login(request)
    try:
        result = await api_request("POST", "/jobs/modify", json={
            "project_id": project_id,
            "change_request": change_request,
        })
        return RedirectResponse(url=f"/jobs/{result['job_id']}", status_code=302)
    except Exception as e:
        logger.error(f"Modify website error: {e}")
        return _redirect_with_error(f"/projects/{project_id}", f"Modifica fallita: {e}")


@app.post("/retry-job/{job_id}")
async def retry_job(request: Request, job_id: str):
    require_login(request)
    try:
        await api_request("POST", f"/jobs/{job_id}/retry")
        return _redirect_with_flash(f"/jobs/{job_id}", "flash", "Retry avviato")
    except Exception as e:
        logger.error(f"Retry job error: {e}")
        return _redirect_with_error(f"/jobs/{job_id}", f"Retry fallito: {e}")


@app.post("/cancel-job/{job_id}")
async def cancel_job(request: Request, job_id: str):
    require_login(request)
    try:
        await api_request("POST", f"/jobs/{job_id}/cancel")
        return _redirect_with_flash(f"/jobs/{job_id}", "flash", "Job cancellato")
    except Exception as e:
        logger.error(f"Cancel job error: {e}")
        return _redirect_with_error(f"/jobs/{job_id}", f"Cancel fallito: {e}")


@app.post("/approve/{project_id}/{revision_id}")
async def approve_revision(request: Request, project_id: str, revision_id: str):
    require_login(request)
    try:
        await api_request("POST", f"/projects/{project_id}/approve", json={
            "revision_id": revision_id,
            "decision": "approved",
        })
        return _redirect_with_flash(f"/projects/{project_id}", "flash", "Revisione approvata")
    except Exception as e:
        logger.error(f"Approve error: {e}")
        return _redirect_with_error(f"/projects/{project_id}", f"Approvazione fallita: {e}")


@app.post("/reject/{project_id}/{revision_id}")
async def reject_revision(request: Request, project_id: str, revision_id: str):
    require_login(request)
    try:
        await api_request("POST", f"/projects/{project_id}/approve", json={
            "revision_id": revision_id,
            "decision": "rejected",
        })
        return _redirect_with_flash(f"/projects/{project_id}", "flash", "Revisione rifiutata")
    except Exception as e:
        logger.error(f"Reject error: {e}")
        return _redirect_with_error(f"/projects/{project_id}", f"Rifiuto fallito: {e}")


@app.post("/rollback/{project_id}")
async def rollback(request: Request, project_id: str, target_revision_id: str = Form(...)):
    require_login(request)
    try:
        await api_request("POST", f"/projects/{project_id}/rollback",
                          params={"target_revision_id": target_revision_id})
        return _redirect_with_flash(f"/projects/{project_id}", "flash", "Rollback completato")
    except Exception as e:
        logger.error(f"Rollback error: {e}")
        return _redirect_with_error(f"/projects/{project_id}", f"Rollback fallito: {e}")


# ---- CMS API proxy ----
# The browser-side CMS editor calls /proxy/cms-api/* and we forward to agent-api
# adding the X-API-Secret header. Only CMS endpoints are whitelisted.

def _is_cms_path(path: str) -> bool:
    """Allow only CMS-related paths to be proxied to agent-api."""
    if not path.startswith("/"):
        return False
    if path.startswith("/cms/"):
        return True
    if path.startswith("/projects/"):
        return "/cms" in path[len("/projects/"):]
    return False


@app.api_route("/proxy/cms-api/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def cms_proxy(request: Request, path: str):
    require_login(request)
    target_path = "/" + path
    if not _is_cms_path(target_path):
        raise HTTPException(status_code=403, detail="Path non autorizzato")

    url = f"{settings.agent_api_url}{target_path}"
    headers = {"X-API-Secret": settings.agent_api_secret}
    params = dict(request.query_params)

    content_type = request.headers.get("content-type", "")
    body = await request.body()
    if "application/json" in content_type and body:
        headers["Content-Type"] = "application/json"
    elif "multipart/form-data" in content_type:
        headers["Content-Type"] = content_type

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.request(
                request.method, url, headers=headers, params=params, content=body,
            )
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            media_type=resp.headers.get("content-type", "application/json"),
        )
    except httpx.RequestError as exc:
        logger.error(f"CMS proxy network error: {exc}")
        raise HTTPException(status_code=502, detail=f"Errore di rete: {exc}") from exc

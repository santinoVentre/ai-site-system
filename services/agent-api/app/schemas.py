"""AI Site System — Pydantic schemas for API requests and responses."""

from __future__ import annotations
from datetime import datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, Field


# ---- Shared ----

class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "agent-api"


# ---- Projects ----

class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    owner_id: Optional[UUID] = None

class ProjectOut(BaseModel):
    id: UUID
    slug: str
    name: str
    description: Optional[str]
    status: str
    current_revision_id: Optional[UUID]
    production_revision_id: Optional[UUID]
    git_repo_path: Optional[str]
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}

class ProjectListOut(BaseModel):
    projects: list[ProjectOut]
    total: int


# ---- Revisions ----

class RevisionOut(BaseModel):
    id: UUID
    project_id: UUID
    revision_number: int
    parent_revision_id: Optional[UUID]
    revision_type: str
    summary: Optional[str]
    change_description: Optional[str]
    files_changed: list
    preview_url: Optional[str]
    production_url: Optional[str]
    status: str
    created_at: datetime
    model_config = {"from_attributes": True}


# ---- Jobs ----

class JobCreate(BaseModel):
    project_id: Optional[UUID] = None
    job_type: str = Field(..., pattern="^(create_website|modify_website|qa_run|deploy)$")
    brief: str = Field(..., min_length=1)
    config: dict = Field(default_factory=dict)

class JobOut(BaseModel):
    id: UUID
    project_id: Optional[UUID]
    revision_id: Optional[UUID]
    job_type: str
    status: str
    brief: Optional[str]
    result: dict
    error_message: Optional[str]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}

class JobEventOut(BaseModel):
    id: UUID
    job_id: UUID
    from_status: Optional[str]
    to_status: str
    agent: Optional[str]
    message: Optional[str]
    created_at: datetime
    model_config = {"from_attributes": True}


# ---- Assets ----

class UploadedAsset(BaseModel):
    """Reference to an image asset uploaded via /assets/upload."""
    asset_id: str
    filename: str
    stored_filename: str
    content_type: str
    size_bytes: int
    asset_type: str = "reference"   # logo | reference | other
    description: str = ""
    path: str


# ---- CMS (custom content sections) ----

class ContentSectionCreate(BaseModel):
    kind: str = Field(..., description="Kind key from KIND_REGISTRY (e.g. 'menu')")
    key: Optional[str] = Field(
        None,
        description="Stable slug used by the rendered site. Auto-generated if omitted.",
    )
    label: Optional[str] = None
    settings: dict = Field(default_factory=dict)
    position: Optional[int] = None
    seed_examples: bool = Field(
        default=False,
        description="If true, seed the section with the kind's example items.",
    )


class ContentSectionUpdate(BaseModel):
    label: Optional[str] = None
    settings: Optional[dict] = None
    position: Optional[int] = None


class ContentSectionOut(BaseModel):
    id: UUID
    project_id: UUID
    kind: str
    key: str
    label: str
    position: int
    settings: dict
    created_at: datetime
    updated_at: datetime
    item_count: int = 0
    model_config = {"from_attributes": True}


class ContentItemCreate(BaseModel):
    data: dict = Field(default_factory=dict)
    position: Optional[int] = None


class ContentItemUpdate(BaseModel):
    data: Optional[dict] = None
    position: Optional[int] = None


class ContentItemOut(BaseModel):
    id: UUID
    section_id: UUID
    position: int
    data: dict
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class ContentImageOut(BaseModel):
    id: UUID
    project_id: UUID
    original_filename: Optional[str]
    stored_filename: str
    mime_type: str
    size_bytes: int
    width: Optional[int]
    height: Optional[int]
    url: str
    alt_text: str = ""
    created_at: datetime
    model_config = {"from_attributes": True}


class ReorderRequest(BaseModel):
    """Reorder items by id (in the desired order)."""
    ids: list[UUID]


# ---- Website Creation ----

class WebsiteCreateRequest(BaseModel):
    """Brief for creating a new website."""
    brief: str = Field(..., min_length=10, description="Website brief / description")
    project_name: Optional[str] = None
    skip_research: bool = False
    uploaded_assets: list[UploadedAsset] = Field(default_factory=list)
    config: dict = Field(default_factory=dict)

class WebsiteCreateResponse(BaseModel):
    job_id: UUID
    project_id: UUID
    project_slug: str
    status: str
    message: str


# ---- Website Modification ----

class WebsiteModifyRequest(BaseModel):
    """Request to modify an existing website."""
    project_id: UUID
    change_request: str = Field(..., min_length=5, description="What to change")
    target_revision_id: Optional[UUID] = None  # defaults to current

class WebsiteModifyResponse(BaseModel):
    job_id: UUID
    project_id: UUID
    change_request_id: UUID
    status: str
    message: str


# ---- Agent Outputs ----

class ProjectSpec(BaseModel):
    """Output of the planner agent."""
    project_name: str
    target_audience: str
    site_type: str  # landing, multi-page, portfolio, etc.
    pages: list[dict]
    design_direction: str
    tone: str
    key_sections: list[str]
    technical_requirements: list[str]
    acceptance_criteria: list[str]

class ResearchReport(BaseModel):
    """Output of the researcher agent."""
    competitors: list[dict]
    design_references: list[dict]
    content_ideas: list[str]
    seo_keywords: list[str]
    recommendations: list[str]

class SiteCopy(BaseModel):
    """Output of the copy agent."""
    pages: dict  # page_name -> { sections: [{heading, body, cta}] }
    meta_titles: dict
    meta_descriptions: dict
    alt_texts: dict

class DesignTokens(BaseModel):
    """Output of the design agent."""
    colors: dict  # primary, secondary, accent, background, text
    typography: dict  # headings_font, body_font, sizes
    spacing: dict
    layout: dict  # max_width, grid, breakpoints
    component_styles: dict

class BuildManifest(BaseModel):
    """Output of the builder agent."""
    files: list[dict]  # [{path, content, type}]
    entry_point: str
    framework: str
    dependencies: list[str]

class ModificationPlan(BaseModel):
    """Output of the modifier agent."""
    change_request_summary: str
    analysis: dict  # current state analysis
    planned_changes: list[dict]  # [{file, action, description, impact}]
    affected_files: list[str]
    risk_assessment: str
    requires_full_rebuild: bool
    rebuild_reason: Optional[str] = None
    acceptance_criteria: list[str]

class RevisionManifest(BaseModel):
    """Post-modification output."""
    revision_number: int
    changed_files: list[dict]  # [{path, action, diff_summary}]
    new_files: list[str]
    deleted_files: list[str]
    migration_notes: list[str]
    summary: str

class QAReportSchema(BaseModel):
    """Output of the QA runner."""
    overall_status: str  # pass, warn, fail
    desktop_score: int
    mobile_score: int
    broken_links: list[dict]
    console_errors: list[dict]
    accessibility_issues: list[dict]
    screenshots: dict  # {desktop: path, mobile: path}
    visual_diff: dict
    details: dict

class DeployStatus(BaseModel):
    """Output of the deploy agent."""
    deploy_type: str  # preview, production
    url: str
    status: str
    deployed_at: Optional[datetime] = None


# ---- Approvals ----

class ApprovalRequest(BaseModel):
    revision_id: UUID
    decision: str = Field(..., pattern="^(approved|rejected)$")
    notes: Optional[str] = None

class ApprovalOut(BaseModel):
    id: UUID
    project_id: UUID
    revision_id: UUID
    decision: Optional[str]
    decided_at: Optional[datetime]
    notes: Optional[str]
    model_config = {"from_attributes": True}


# ---- Change Requests ----

class ChangeRequestOut(BaseModel):
    id: UUID
    project_id: UUID
    target_revision_id: UUID
    resulting_revision_id: Optional[UUID]
    request_text: str
    parsed_intent: dict
    impacted_files: list
    modification_plan: dict
    status: str
    created_at: datetime
    model_config = {"from_attributes": True}


# ---- QA ----

class QARunRequest(BaseModel):
    revision_id: UUID
    preview_url: str

class QAReportOut(BaseModel):
    id: UUID
    revision_id: UUID
    overall_status: str
    desktop_score: Optional[int]
    mobile_score: Optional[int]
    broken_links: list
    console_errors: list
    accessibility_issues: list
    screenshots: dict
    created_at: datetime
    model_config = {"from_attributes": True}

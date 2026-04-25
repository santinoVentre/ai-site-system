"""AI Site System — SQLAlchemy ORM models."""

import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Text, Boolean, Integer, ForeignKey, DateTime, Index
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username = Column(String(100), unique=True, nullable=False)
    email = Column(String(255))
    telegram_chat_id = Column(String(50), index=True)
    role = Column(String(20), nullable=False, default="operator")
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    projects = relationship("Project", back_populates="owner", foreign_keys="Project.owner_id")


class Project(Base):
    __tablename__ = "projects"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug = Column(String(100), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    current_revision_id = Column(UUID(as_uuid=True), ForeignKey("project_revisions.id", use_alter=True))
    production_revision_id = Column(UUID(as_uuid=True), ForeignKey("project_revisions.id", use_alter=True))
    status = Column(String(30), nullable=False, default="active", index=True)
    git_repo_path = Column(String(500))
    metadata_ = Column("metadata", JSONB, default={})
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    owner = relationship("User", back_populates="projects", foreign_keys=[owner_id])
    revisions = relationship("ProjectRevision", back_populates="project", foreign_keys="ProjectRevision.project_id")
    jobs = relationship("Job", back_populates="project")
    change_requests = relationship("ChangeRequest", back_populates="project")


class ProjectRevision(Base):
    __tablename__ = "project_revisions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    revision_number = Column(Integer, nullable=False)
    parent_revision_id = Column(UUID(as_uuid=True), ForeignKey("project_revisions.id"))
    git_commit_hash = Column(String(64))
    revision_type = Column(String(20), nullable=False, default="create")
    summary = Column(Text)
    change_description = Column(Text)
    files_changed = Column(JSONB, default=[])
    diff_summary = Column(JSONB, default={})
    source_path = Column(String(500))
    preview_url = Column(String(500))
    production_url = Column(String(500))
    status = Column(String(30), nullable=False, default="draft", index=True)
    approved_at = Column(DateTime(timezone=True))
    approved_by = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    project = relationship("Project", back_populates="revisions", foreign_keys=[project_id])
    qa_reports = relationship("QAReport", back_populates="revision")


class Job(Base):
    __tablename__ = "jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id"), index=True)
    revision_id = Column(UUID(as_uuid=True), ForeignKey("project_revisions.id"))
    job_type = Column(String(30), nullable=False, index=True)
    status = Column(String(30), nullable=False, default="new", index=True)
    brief = Column(Text)
    config = Column(JSONB, default={})
    result = Column(JSONB, default={})
    error_message = Column(Text)
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    project = relationship("Project", back_populates="jobs")
    events = relationship("JobEvent", back_populates="job", order_by="JobEvent.created_at")
    artifacts = relationship("Artifact", back_populates="job")


class JobEvent(Base):
    __tablename__ = "job_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    from_status = Column(String(30))
    to_status = Column(String(30), nullable=False)
    agent = Column(String(50))
    message = Column(Text)
    payload = Column(JSONB, default={})
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, index=True)

    job = relationship("Job", back_populates="events")


class Artifact(Base):
    __tablename__ = "artifacts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    revision_id = Column(UUID(as_uuid=True), ForeignKey("project_revisions.id"), index=True)
    artifact_type = Column(String(50), nullable=False, index=True)
    file_path = Column(String(500))
    content = Column(JSONB)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    job = relationship("Job", back_populates="artifacts")


class QAReport(Base):
    __tablename__ = "qa_reports"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    revision_id = Column(UUID(as_uuid=True), ForeignKey("project_revisions.id"), nullable=False, index=True)
    overall_status = Column(String(20), nullable=False, default="pending", index=True)
    desktop_score = Column(Integer)
    mobile_score = Column(Integer)
    broken_links = Column(JSONB, default=[])
    console_errors = Column(JSONB, default=[])
    accessibility_issues = Column(JSONB, default=[])
    screenshots = Column(JSONB, default={})
    visual_diff = Column(JSONB, default={})
    details = Column(JSONB, default={})
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    revision = relationship("ProjectRevision", back_populates="qa_reports")


class ChangeRequest(Base):
    __tablename__ = "change_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False, index=True)
    target_revision_id = Column(UUID(as_uuid=True), ForeignKey("project_revisions.id"), nullable=False)
    resulting_revision_id = Column(UUID(as_uuid=True), ForeignKey("project_revisions.id"))
    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id"))
    request_text = Column(Text, nullable=False)
    parsed_intent = Column(JSONB, default={})
    impacted_files = Column(JSONB, default=[])
    modification_plan = Column(JSONB, default={})
    diff_summary = Column(JSONB, default={})
    status = Column(String(30), nullable=False, default="pending", index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    project = relationship("Project", back_populates="change_requests")


class Deployment(Base):
    __tablename__ = "deployments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False, index=True)
    revision_id = Column(UUID(as_uuid=True), ForeignKey("project_revisions.id"), nullable=False, index=True)
    deploy_type = Column(String(20), nullable=False)
    deploy_url = Column(String(500))
    status = Column(String(20), nullable=False, default="pending")
    deployed_at = Column(DateTime(timezone=True))
    removed_at = Column(DateTime(timezone=True))
    metadata_ = Column("metadata", JSONB, default={})
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)


class Approval(Base):
    __tablename__ = "approvals"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False)
    revision_id = Column(UUID(as_uuid=True), ForeignKey("project_revisions.id"), nullable=False, index=True)
    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id"))
    requested_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    decision = Column(String(20))
    decided_by = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    decided_at = Column(DateTime(timezone=True))
    notes = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)


# ============================================================================
# CMS — dynamic content editable from the admin dashboard
# ============================================================================

class ContentSection(Base):
    """A typed content collection attached to a project (e.g. menu, hours, faq).

    The `kind` matches a key in `app.cms.kinds.KIND_REGISTRY`. The `key` is a
    stable slug (unique per project) used to reference the section from the
    generated site (e.g. `data-cms-section="menu"`).
    """

    __tablename__ = "content_sections"
    __table_args__ = (
        Index("ix_content_sections_project_key", "project_id", "key", unique=True),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind = Column(String(50), nullable=False, index=True)
    key = Column(String(80), nullable=False)
    label = Column(String(255), nullable=False)
    position = Column(Integer, nullable=False, default=0)
    settings = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    items = relationship(
        "ContentItem",
        back_populates="section",
        cascade="all, delete-orphan",
        order_by="ContentItem.position",
    )


class ContentItem(Base):
    """A single item inside a `ContentSection` (e.g. one dish in a menu)."""

    __tablename__ = "content_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    section_id = Column(
        UUID(as_uuid=True),
        ForeignKey("content_sections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    position = Column(Integer, nullable=False, default=0)
    data = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    section = relationship("ContentSection", back_populates="items")


class ContentImage(Base):
    """An image uploaded by the customer through the CMS.

    Files live on disk under `<cms_assets_path>/<project_slug>/...`. We store
    metadata (dimensions, mime, size) so the dashboard can show thumbnails
    without re-reading the file.
    """

    __tablename__ = "content_images"
    __table_args__ = (
        Index("ix_content_images_project_created", "project_id", "created_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    original_filename = Column(String(500))
    stored_filename = Column(String(500), nullable=False)
    mime_type = Column(String(100), nullable=False)
    size_bytes = Column(Integer, nullable=False, default=0)
    width = Column(Integer)
    height = Column(Integer)
    url = Column(String(500), nullable=False)
    alt_text = Column(String(500), default="")
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

-- =============================================================
--  AI Site System — Application Schema
--  Applied to the app database after init-db.sh creates it
--  This file runs as the postgres superuser on the app database
-- =============================================================

-- Grant privileges to app_user
GRANT ALL PRIVILEGES ON SCHEMA public TO app_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO app_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO app_user;

-- =============================================================
--  Application Schema
-- =============================================================

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ---- Users ----
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    username VARCHAR(100) UNIQUE NOT NULL,
    email VARCHAR(255),
    telegram_chat_id VARCHAR(50),
    role VARCHAR(20) NOT NULL DEFAULT 'operator',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_users_telegram ON users(telegram_chat_id);

-- ---- Projects ----
CREATE TABLE projects (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    slug VARCHAR(100) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    owner_id UUID REFERENCES users(id),
    current_revision_id UUID,  -- FK added after revisions table
    production_revision_id UUID,  -- FK added after revisions table
    status VARCHAR(30) NOT NULL DEFAULT 'active',
    git_repo_path VARCHAR(500),
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_projects_slug ON projects(slug);
CREATE INDEX idx_projects_status ON projects(status);

-- ---- Project Revisions ----
CREATE TABLE project_revisions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    revision_number INTEGER NOT NULL,
    parent_revision_id UUID REFERENCES project_revisions(id),
    git_commit_hash VARCHAR(64),
    revision_type VARCHAR(20) NOT NULL DEFAULT 'create',  -- create, modify, rollback
    summary TEXT,
    change_description TEXT,
    files_changed JSONB DEFAULT '[]',
    diff_summary JSONB DEFAULT '{}',
    source_path VARCHAR(500),
    preview_url VARCHAR(500),
    production_url VARCHAR(500),
    status VARCHAR(30) NOT NULL DEFAULT 'draft',  -- draft, preview, approved, live, superseded, rolled_back
    approved_at TIMESTAMPTZ,
    approved_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(project_id, revision_number)
);

CREATE INDEX idx_revisions_project ON project_revisions(project_id);
CREATE INDEX idx_revisions_status ON project_revisions(status);

-- Now add FK constraints to projects
ALTER TABLE projects ADD CONSTRAINT fk_current_revision
    FOREIGN KEY (current_revision_id) REFERENCES project_revisions(id);
ALTER TABLE projects ADD CONSTRAINT fk_production_revision
    FOREIGN KEY (production_revision_id) REFERENCES project_revisions(id);

-- ---- Jobs ----
CREATE TABLE jobs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id UUID REFERENCES projects(id),
    revision_id UUID REFERENCES project_revisions(id),
    job_type VARCHAR(30) NOT NULL,  -- create_website, modify_website, qa_run, deploy
    status VARCHAR(30) NOT NULL DEFAULT 'new',
    -- States: new, planning, researching, writing, designing, building,
    --         modifying, qa, review, preview_ready, awaiting_approval,
    --         deploying, deployed, failed, rolled_back
    brief TEXT,
    config JSONB DEFAULT '{}',
    result JSONB DEFAULT '{}',
    error_message TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_jobs_project ON jobs(project_id);
CREATE INDEX idx_jobs_status ON jobs(status);
CREATE INDEX idx_jobs_type ON jobs(job_type);

-- ---- Job Events (state transitions + logs) ----
CREATE TABLE job_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    job_id UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    from_status VARCHAR(30),
    to_status VARCHAR(30) NOT NULL,
    agent VARCHAR(50),
    message TEXT,
    payload JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_job_events_job ON job_events(job_id);
CREATE INDEX idx_job_events_created ON job_events(created_at);

-- ---- Artifacts (structured JSON outputs from agents) ----
CREATE TABLE artifacts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    job_id UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    revision_id UUID REFERENCES project_revisions(id),
    artifact_type VARCHAR(50) NOT NULL,
    -- Types: project_spec, research_report, site_copy, design_tokens,
    --        build_manifest, modification_plan, revision_manifest,
    --        qa_report, deploy_status
    file_path VARCHAR(500),
    content JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_artifacts_job ON artifacts(job_id);
CREATE INDEX idx_artifacts_revision ON artifacts(revision_id);
CREATE INDEX idx_artifacts_type ON artifacts(artifact_type);

-- ---- QA Reports ----
CREATE TABLE qa_reports (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    job_id UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    revision_id UUID NOT NULL REFERENCES project_revisions(id),
    overall_status VARCHAR(20) NOT NULL DEFAULT 'pending',  -- pending, pass, warn, fail
    desktop_score INTEGER,
    mobile_score INTEGER,
    broken_links JSONB DEFAULT '[]',
    console_errors JSONB DEFAULT '[]',
    accessibility_issues JSONB DEFAULT '[]',
    screenshots JSONB DEFAULT '{}',
    visual_diff JSONB DEFAULT '{}',
    details JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_qa_reports_revision ON qa_reports(revision_id);
CREATE INDEX idx_qa_reports_status ON qa_reports(overall_status);

-- ---- Change Requests (modification requests) ----
CREATE TABLE change_requests (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id UUID NOT NULL REFERENCES projects(id),
    target_revision_id UUID NOT NULL REFERENCES project_revisions(id),
    resulting_revision_id UUID REFERENCES project_revisions(id),
    job_id UUID REFERENCES jobs(id),
    request_text TEXT NOT NULL,
    parsed_intent JSONB DEFAULT '{}',
    impacted_files JSONB DEFAULT '[]',
    modification_plan JSONB DEFAULT '{}',
    diff_summary JSONB DEFAULT '{}',
    status VARCHAR(30) NOT NULL DEFAULT 'pending',
    -- pending, planning, in_progress, qa, preview, approved, rejected, applied
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_change_requests_project ON change_requests(project_id);
CREATE INDEX idx_change_requests_status ON change_requests(status);

-- ---- Deployments ----
CREATE TABLE deployments (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id UUID NOT NULL REFERENCES projects(id),
    revision_id UUID NOT NULL REFERENCES project_revisions(id),
    deploy_type VARCHAR(20) NOT NULL,  -- preview, production
    deploy_url VARCHAR(500),
    status VARCHAR(20) NOT NULL DEFAULT 'pending',  -- pending, deploying, live, failed, removed
    deployed_at TIMESTAMPTZ,
    removed_at TIMESTAMPTZ,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_deployments_project ON deployments(project_id);
CREATE INDEX idx_deployments_revision ON deployments(revision_id);

-- ---- CMS: Content Sections ----
CREATE TABLE IF NOT EXISTS content_sections (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind VARCHAR(50) NOT NULL,
    key VARCHAR(80) NOT NULL,
    label VARCHAR(255) NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    settings JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_content_sections_project ON content_sections(project_id);
CREATE INDEX IF NOT EXISTS idx_content_sections_kind ON content_sections(kind);
CREATE UNIQUE INDEX IF NOT EXISTS ix_content_sections_project_key ON content_sections(project_id, key);

-- ---- CMS: Content Items ----
CREATE TABLE IF NOT EXISTS content_items (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    section_id UUID NOT NULL REFERENCES content_sections(id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    data JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_content_items_section ON content_items(section_id);

-- ---- CMS: Content Images ----
CREATE TABLE IF NOT EXISTS content_images (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    original_filename VARCHAR(500),
    stored_filename VARCHAR(500) NOT NULL,
    mime_type VARCHAR(100) NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    width INTEGER,
    height INTEGER,
    url VARCHAR(500) NOT NULL,
    alt_text VARCHAR(500) DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_content_images_project ON content_images(project_id);
CREATE INDEX IF NOT EXISTS ix_content_images_project_created ON content_images(project_id, created_at);

-- Apply updated_at triggers for CMS
CREATE TRIGGER trg_content_sections_updated BEFORE UPDATE ON content_sections FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER trg_content_items_updated BEFORE UPDATE ON content_items FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ---- Approvals ----
CREATE TABLE approvals (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id UUID NOT NULL REFERENCES projects(id),
    revision_id UUID NOT NULL REFERENCES project_revisions(id),
    job_id UUID REFERENCES jobs(id),
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    decision VARCHAR(20),  -- approved, rejected
    decided_by UUID REFERENCES users(id),
    decided_at TIMESTAMPTZ,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_approvals_revision ON approvals(revision_id);
CREATE INDEX idx_approvals_decision ON approvals(decision);

-- ---- Updated_at trigger function ----
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Apply updated_at triggers
CREATE TRIGGER trg_users_updated BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER trg_projects_updated BEFORE UPDATE ON projects FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER trg_revisions_updated BEFORE UPDATE ON project_revisions FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER trg_jobs_updated BEFORE UPDATE ON jobs FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER trg_change_requests_updated BEFORE UPDATE ON change_requests FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ---- Seed default admin user ----
INSERT INTO users (username, role) VALUES ('admin', 'admin')
ON CONFLICT (username) DO NOTHING;

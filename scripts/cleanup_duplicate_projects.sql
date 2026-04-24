-- Cleanup duplicate projects, keep only 874193cb-55fd-4e97-8583-8e0808e6b6ad
-- Run order matters due to FK constraints

DELETE FROM artifacts
WHERE job_id IN (
    SELECT id FROM jobs
    WHERE project_id != '874193cb-55fd-4e97-8583-8e0808e6b6ad'
);

DELETE FROM job_events
WHERE job_id IN (
    SELECT id FROM jobs
    WHERE project_id != '874193cb-55fd-4e97-8583-8e0808e6b6ad'
);

DELETE FROM change_requests
WHERE project_id != '874193cb-55fd-4e97-8583-8e0808e6b6ad';

DELETE FROM jobs
WHERE project_id != '874193cb-55fd-4e97-8583-8e0808e6b6ad';

DELETE FROM project_revisions
WHERE project_id != '874193cb-55fd-4e97-8583-8e0808e6b6ad';

DELETE FROM projects
WHERE id != '874193cb-55fd-4e97-8583-8e0808e6b6ad';

SELECT COUNT(*) AS remaining_projects FROM projects;
SELECT id, name, slug FROM projects;

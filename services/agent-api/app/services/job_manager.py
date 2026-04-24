"""Job manager — handles job state transitions and event logging."""

import logging
from datetime import datetime
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Job, JobEvent

logger = logging.getLogger(__name__)

# All in-flight states where a job can legally be cancelled
ACTIVE_STATES = (
    "new",
    "planning",
    "researching",
    "writing",
    "designing",
    "building",
    "modifying",
    "qa",
    "review",
)

# Valid state transitions. "failed" is always reachable from any active state.
VALID_TRANSITIONS = {
    "new": ["planning", "modifying", "failed", "cancelled"],
    "planning": ["researching", "writing", "failed", "cancelled"],
    "researching": ["writing", "failed", "cancelled"],
    "writing": ["designing", "failed", "cancelled"],
    "designing": ["building", "failed", "cancelled"],
    "building": ["qa", "review", "failed", "cancelled"],
    "modifying": ["qa", "building", "failed", "cancelled"],
    "qa": ["review", "preview_ready", "building", "failed", "cancelled"],
    "review": ["preview_ready", "building", "modifying", "failed", "cancelled"],
    "preview_ready": ["awaiting_approval", "failed"],
    "awaiting_approval": ["deploying", "review", "failed"],
    "deploying": ["deployed", "failed"],
    "deployed": ["rolled_back"],
    "failed": ["new"],  # allow retry
    "cancelled": ["new"],  # allow retry from cancelled
    "rolled_back": [],
}


async def transition_job(
    db: AsyncSession,
    job_id: UUID,
    to_status: str,
    agent: str | None = None,
    message: str | None = None,
    payload: dict | None = None,
) -> Job:
    """Transition a job to a new state, logging the event."""
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one()

    from_status = job.status

    if to_status not in VALID_TRANSITIONS.get(from_status, []):
        raise ValueError(f"Invalid transition: {from_status} -> {to_status}")

    job.status = to_status
    if to_status == "new":
        pass
    elif from_status == "new":
        job.started_at = datetime.utcnow()
    if to_status in ("deployed", "failed", "rolled_back", "cancelled"):
        job.completed_at = datetime.utcnow()

    event = JobEvent(
        job_id=job_id,
        from_status=from_status,
        to_status=to_status,
        agent=agent,
        message=message,
        payload=payload or {},
    )
    db.add(event)
    await db.flush()

    logger.info(f"Job {job_id}: {from_status} -> {to_status} (agent={agent})")
    return job


async def set_job_error(
    db: AsyncSession,
    job_id: UUID,
    error_message: str,
    agent: str | None = None,
) -> Job:
    """Mark a job as failed with an error message. Always emits a JobEvent."""
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one()

    # Capture the real prior status BEFORE mutating.
    from_status = job.status

    job.error_message = error_message
    job.status = "failed"
    job.completed_at = datetime.utcnow()

    event = JobEvent(
        job_id=job_id,
        from_status=from_status,
        to_status="failed",
        agent=agent,
        message=error_message,
    )
    db.add(event)
    await db.flush()

    logger.error(f"Job {job_id} failed (from {from_status}): {error_message}")
    return job


async def cancel_job(
    db: AsyncSession,
    job_id: UUID,
    reason: str = "Cancelled by admin",
) -> Job:
    """Cancel an in-flight job. Safe to call from any active state."""
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one()

    from_status = job.status
    if from_status not in ACTIVE_STATES:
        raise ValueError(f"Cannot cancel a job in status '{from_status}'")

    job.status = "cancelled"
    job.error_message = reason
    job.completed_at = datetime.utcnow()

    event = JobEvent(
        job_id=job_id,
        from_status=from_status,
        to_status="cancelled",
        agent="system",
        message=reason,
    )
    db.add(event)
    await db.flush()

    logger.info(f"Job {job_id} cancelled (from {from_status}): {reason}")
    return job

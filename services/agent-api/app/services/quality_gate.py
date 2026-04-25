"""Quality gate — reviewer loop that iterates the builder up to N times if score < threshold.

Minimal stub now; full implementation arrives in phase3 (structured score + issues,
targeted rebuild prompt including `review_issues`).
"""

from __future__ import annotations

import logging
from typing import Any

from app.agents.builder import run_builder
from app.agents.reviewer import run_reviewer
from app.config import get_settings
from app.services.git_manager import commit_revision

logger = logging.getLogger(__name__)
settings = get_settings()


def _score_from_legacy(review: dict) -> int:
    """Convert legacy overall_quality string to a numeric 0-100 score."""
    mapping = {"good": 90, "acceptable": 78, "needs_work": 60, "poor": 40}
    return mapping.get(review.get("overall_quality", ""), 75)


async def run_quality_gate(
    *,
    initial_files: list[dict[str, Any]] | dict[str, Any],
    project_spec: dict,
    site_copy: dict,
    design_tokens: dict,
    image_map: dict[str, str] | None = None,
    project_slug: str,
    revision_number: int,
    cms_data: dict[str, dict] | None = None,
    cms_data_url: str | None = None,
) -> tuple[dict, list[dict] | None]:
    """Run reviewer, optionally rebuild up to `quality_max_iterations` if score is low.

    Returns (review_report, final_files_or_none).
    `final_files` is set only if the gate rebuilt the site.
    """
    threshold = int(settings.quality_score_threshold or 80)
    max_iters = int(settings.quality_max_iterations or 2)

    files = initial_files
    review: dict = {}
    iterations = 0
    last_files: list[dict] | None = None

    for i in range(max_iters + 1):
        files_for_review = files if isinstance(files, list) else []
        review = await run_reviewer(
            files=files_for_review,
            project_spec=project_spec,
        ) or {}
        score = int(review.get("score") or _score_from_legacy(review))
        issues = review.get("issues") or []
        review["iterations"] = i
        review["score"] = score
        logger.info("Quality gate iter %d: score=%d (threshold=%d)", i, score, threshold)

        if score >= threshold or i >= max_iters:
            break

        logger.info("Score below threshold — triggering builder rebuild iter %d", i + 1)
        try:
            manifest = await run_builder(
                project_spec=project_spec,
                site_copy=site_copy,
                design_tokens=design_tokens,
                image_urls=image_map or {},
                project_slug=project_slug,
                cms_data=cms_data,
                cms_data_url=cms_data_url,
                review_issues=issues,
            )
            last_files = manifest.get("files") or []
            files = last_files
            iterations = i + 1
        except Exception as exc:
            logger.warning("Quality gate rebuild iter %d failed: %s", i + 1, exc)
            break

    review["iterations"] = iterations
    return review, last_files

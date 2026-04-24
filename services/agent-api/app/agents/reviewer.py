"""Reviewer agent — structured quality review with numeric score + categorised issues.

Output contract (versioned):
{
  "version": 2,
  "score": 0-100,
  "overall_quality": "good|acceptable|needs_work|poor",
  "category_scores": {"a11y":0-100, "seo":0-100, "responsive":0-100, "copy":0-100, "design":0-100, "performance":0-100},
  "issues": [
    {"severity":"critical|major|minor","category":"a11y|seo|responsive|copy|design|performance","file":"path|null","line":null,"description":"...","suggested_fix":"..."}
  ],
  "strengths": ["..."],
  "ready_for_preview": true|false
}

The builder consumes `issues` as `review_issues` on rebuild iterations.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.services.llm_client import call_llm

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are a senior web quality reviewer for an AI website builder. You return a structured JSON audit of the provided site files.

Grade the site 0-100 across six categories:
- a11y: semantic HTML, landmarks, alt text, labels, focus states, color contrast
- seo: titles, meta descriptions, canonical, Open Graph, structured data, heading hierarchy, sitemap
- responsive: mobile-first layout, breakpoints, no horizontal overflow, touch targets
- copy: clarity, on-brand tone, grammar, absence of lorem ipsum/placeholders
- design: visual hierarchy, spacing, rhythm, polish, consistency of tokens
- performance: inline script size, lazy-loading images, avoidable duplication

Produce at most 20 issues, each grounded in an observation from the files you read. Use severity:
- critical: violates a11y/WCAG AA or breaks the site on mobile
- major: clear UX/SEO defect that a paying client would reject
- minor: polish / nice-to-have

Suggested_fix must be concise and actionable (≤ 120 chars).

Return ONLY this JSON (no markdown, no preamble):
{
  "version": 2,
  "score": <0-100 integer>,
  "overall_quality": "good|acceptable|needs_work|poor",
  "category_scores": {"a11y": 0-100, "seo": 0-100, "responsive": 0-100, "copy": 0-100, "design": 0-100, "performance": 0-100},
  "issues": [ {"severity":"critical|major|minor","category":"a11y|seo|responsive|copy|design|performance","file":"path|null","line":null,"description":"...","suggested_fix":"..."} ],
  "strengths": ["string", ...],
  "ready_for_preview": true|false
}
"""


_ALLOWED_SEVERITIES = {"critical", "major", "minor", "warning", "info"}
_ALLOWED_CATEGORIES = {"a11y", "seo", "responsive", "copy", "design", "performance"}


def _summarise_file(path: str, content: str, max_chars: int = 12000) -> str:
    """Keep small files whole; for large ones, take head+tail with a marker."""
    if len(content) <= max_chars:
        return content
    head = content[: max_chars // 2]
    tail = content[-max_chars // 2 :]
    return f"{head}\n\n<!-- … {len(content) - max_chars} chars omitted … -->\n\n{tail}"


def _quality_from_score(score: int) -> str:
    if score >= 90:
        return "good"
    if score >= 78:
        return "acceptable"
    if score >= 60:
        return "needs_work"
    return "poor"


def _normalise_issues(raw: Any) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        sev = (item.get("severity") or "minor").lower()
        cat = (item.get("category") or "design").lower()
        out.append(
            {
                "severity": sev if sev in _ALLOWED_SEVERITIES else "minor",
                "category": cat if cat in _ALLOWED_CATEGORIES else "design",
                "file": item.get("file"),
                "line": item.get("line"),
                "description": (item.get("description") or "").strip(),
                "suggested_fix": (item.get("suggested_fix") or item.get("suggestion") or "").strip(),
            }
        )
    return out


def _ensure_shape(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        result = {}
    score = result.get("score")
    try:
        score = int(score)
    except Exception:
        score = None
    if score is None:
        # Fallback from legacy overall_quality strings
        score = {"good": 90, "acceptable": 78, "needs_work": 60, "poor": 40}.get(
            result.get("overall_quality", ""), 75
        )
    score = max(0, min(100, score))

    cat_scores = result.get("category_scores") or {}
    for c in _ALLOWED_CATEGORIES:
        try:
            cat_scores[c] = max(0, min(100, int(cat_scores.get(c, score))))
        except Exception:
            cat_scores[c] = score

    issues = _normalise_issues(result.get("issues"))

    return {
        "version": 2,
        "score": score,
        "overall_quality": result.get("overall_quality") or _quality_from_score(score),
        "category_scores": cat_scores,
        "issues": issues,
        "strengths": result.get("strengths") or [],
        "ready_for_preview": bool(result.get("ready_for_preview", score >= 70)),
    }


async def run_reviewer(
    files: list[dict] | None = None,
    project_spec: dict | None = None,
    **_ignored: Any,
) -> dict:
    """Review the given site files. Accepts list of {path, content} dicts."""
    logger.info("Running reviewer agent (v2)")

    parts: list[str] = []
    for f in files or []:
        path = f.get("path", "(unknown)")
        ext = Path(path).suffix.lower()
        if ext in {".png", ".jpg", ".jpeg", ".svg", ".webp"}:
            continue
        parts.append(f"--- {path} ---\n{_summarise_file(path, f.get('content', ''))}")
    joined = "\n\n".join(parts) if parts else "(no files)"

    user_prompt = f"SITE FILES:\n{joined}"
    if project_spec:
        user_prompt += f"\n\nPROJECT SPEC:\n{project_spec}"

    try:
        result = await call_llm(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            response_format="json",
            temperature=0.25,
            max_tokens=2048,
        )
    except Exception as exc:
        logger.warning("Reviewer LLM failed: %s", exc)
        result = {}

    normalised = _ensure_shape(result)
    logger.info(
        "Review: score=%d, overall=%s, issues=%d",
        normalised["score"],
        normalised["overall_quality"],
        len(normalised["issues"]),
    )
    return normalised

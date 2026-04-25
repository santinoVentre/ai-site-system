"""Builder agent — assembles the final website deterministically from LayoutPlan + Jinja templates.

Flow:
  1. `run_layout_planner` produces a LayoutPlan (sections + variants per page) via LLM.
  2. Python assembles the multi-page site from the section template catalog.
  3. If the LayoutPlan declares `custom` sections (no catalog variant), an isolated LLM
     fallback fills in just that section.
  4. SEO (meta, JSON-LD, sitemap.xml, robots.txt) and a11y are baked into the base template.

NOTE: This file replaces the previous free-form HTML-generating builder.
"""

from __future__ import annotations

import html as _html
import logging
import re
from typing import Any

from app.agents.layout_planner import run_layout_planner
from app.agents.sections import assemble_site
from app.services.llm_client import call_llm

logger = logging.getLogger(__name__)


_CUSTOM_SYSTEM_PROMPT = """You are a senior frontend engineer. Produce a single HTML section (no <html>, no <body>) using Tailwind CSS utility classes and Alpine.js directives if needed. Respect the site's design tokens. Do not include <style> blocks. Output ONLY the <section>...</section> markup, no markdown fences, no commentary."""


def _sanitize_html(content: str) -> str:
    """Normalise curly apostrophes and stray script payloads that routinely break generated sites."""
    if not content:
        return content
    # Normalise curly quotes to straight ones ONLY inside script tags to avoid SyntaxError
    def _fix_script(match: re.Match) -> str:
        body = match.group(2)
        body = body.replace("\u2018", "'").replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')
        return f"{match.group(1)}{body}</script>"

    return re.sub(r"(<script\b[^>]*>)(.*?)</script>", _fix_script, content, flags=re.DOTALL | re.IGNORECASE)


async def _render_custom_section(
    section: dict[str, Any],
    *,
    design_tokens: dict[str, Any],
    copy_payload: dict[str, Any],
) -> str:
    """Fallback: ask LLM for an isolated section when no catalog variant fits."""
    prompt = (
        "Design tokens:\n"
        f"{design_tokens}\n\n"
        "Copy for this section:\n"
        f"{copy_payload}\n\n"
        "Section requirement:\n"
        f"{section}\n"
    )
    try:
        html = await call_llm(
            system_prompt=_CUSTOM_SYSTEM_PROMPT,
            user_prompt=prompt,
            response_format=None,
            temperature=0.35,
            max_tokens=1400,
        )
        return html if isinstance(html, str) else str(html)
    except Exception as exc:
        logger.warning("Custom section fallback LLM failed: %s", exc)
        sid = section.get("id") or "custom"
        return f"<section id=\"{sid}\" class=\"py-16 text-center text-slate-500\"><p>Sezione in preparazione…</p></section>"


async def _merge_custom_sections(layout_plan: dict, *, design_tokens: dict, site_copy: dict) -> dict:
    """Scan the LayoutPlan for any `custom` section markers and generate them inline.

    Returns the same LayoutPlan with each custom section's HTML cached into a
    per-section `inline_html` field, which assembly will detect and use as-is.
    """
    for page in layout_plan.get("pages") or []:
        for section in page.get("sections") or []:
            if section.get("type") != "custom":
                continue
            if section.get("inline_html"):
                continue
            section["inline_html"] = await _render_custom_section(
                section,
                design_tokens=design_tokens,
                copy_payload=(site_copy.get("sections") or {}).get(section.get("id") or "", {}),
            )
    return layout_plan


async def run_builder(
    project_spec: dict,
    site_copy: dict,
    design_tokens: dict,
    project_slug: str | None = None,
    image_urls: dict[str, str] | None = None,
    cms_data: dict[str, dict] | None = None,
    cms_data_url: str | None = None,
    review_issues: list[dict] | None = None,
) -> dict:
    """Run the builder.

    Pipeline:
      LayoutPlan (LLM) → Python/Jinja assembly → per-file manifest.
    """
    logger.info("Running builder agent (catalog-driven)")

    layout_plan = await run_layout_planner(
        project_spec=project_spec,
        site_copy=site_copy,
        design_tokens=design_tokens,
        review_issues=review_issues,
    )

    # Handle optional `custom` section fallback BEFORE assembly
    layout_plan = await _merge_custom_sections(
        layout_plan,
        design_tokens=design_tokens,
        site_copy=site_copy,
    )

    # If we have review_issues from a previous iteration, fold them into copy/design as hints
    if review_issues:
        logger.info("Builder received %d review issues to address", len(review_issues))
        site_copy = dict(site_copy)
        site_copy.setdefault("_review_issues", review_issues)

    site_files_map = assemble_site(
        project_spec=project_spec,
        site_copy=site_copy,
        design_tokens=design_tokens,
        layout_plan=layout_plan,
        image_urls=image_urls or {},
        cms_data=cms_data,
        cms_data_url=cms_data_url,
    )

    files = []
    for path, content in site_files_map.items():
        if path.endswith(".html"):
            content = _sanitize_html(content)
        ext = path.rsplit(".", 1)[-1].lower() if "." in path else "txt"
        type_map = {"html": "html", "css": "css", "js": "js", "json": "json", "svg": "svg", "xml": "xml", "txt": "txt"}
        files.append({"path": path, "content": content, "type": type_map.get(ext, "txt")})

    entry = next((f["path"] for f in files if f["path"].endswith("index.html")), files[0]["path"] if files else "index.html")

    result = {
        "files": files,
        "entry_point": entry,
        "framework": "static-tailwind-alpine",
        "dependencies": [],
        "layout_plan": layout_plan,
    }

    logger.info("Builder produced %d files (entry: %s)", len(files), entry)
    return result


__all__ = ["run_builder"]

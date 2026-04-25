"""Layout Planner — LLM-driven picker that chooses sections + variants for each page.

Output: a LayoutPlan JSON consumed by the section assembler. The catalog describes
every section/variant available, so the LLM simply orders and parameterises them.
No free-form HTML here.
"""

from __future__ import annotations

import logging
from typing import Any

from app.agents.sections.catalog import SECTION_CATALOG, catalog_summary
from app.cms import KIND_REGISTRY, section_template_for
from app.services.llm_client import call_llm

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are a senior web design director. Given a project spec, copy, and a catalog of pre-built sections, compose a LayoutPlan.

Rules:
- Use ONLY section types and variants present in the catalog. Do NOT invent names.
- Each page should have a coherent flow: navbar first, footer last, with 4-8 body sections between.
- Pick variants that match the tone/site_type (luxury → minimal_centered hero; restaurant → dynamic_menu; SaaS → pricing tiers, features grid).
- For every dynamic section listed in the project spec, include exactly one matching `dynamic_*` section with `cms_key` equal to the dynamic section `key` (the right `dynamic_*` type is determined by the section `kind`; do not invent your own variant).
- Every section must have a stable `id` (lowercase snake_case). Use predictable ids like `hero`, `services`, `about`, `team`, `testimonials`, `pricing`, `faq`, `contact`.
- If the project_spec has multiple pages, build one entry per page. Otherwise output a single-page site.
- Keep the layout deterministic and professional — no duplicate sections of the same type on the same page.

Output EXACT JSON shape:
{
  "pages": [
    {
      "slug": "index",
      "title": "Home",
      "meta_title": "...",
      "meta_description": "...",
      "sections": [
        {"type": "navbar", "variant": "sticky_glass", "id": "navbar"},
        {"type": "hero",   "variant": "split_image",  "id": "hero", "image_query": "optional string"},
        ...
        {"type": "footer", "variant": "multicol",     "id": "footer"}
      ]
    }
  ]
}

Return ONLY valid JSON, no commentary.
"""


def _normalise_layout(layout: dict[str, Any], project_spec: dict) -> dict[str, Any]:
    """Validate and repair the LayoutPlan so it always conforms to the catalog."""
    pages = layout.get("pages") or []
    if not isinstance(pages, list) or not pages:
        pages = [
            {
                "slug": "index",
                "title": project_spec.get("project_name", "Home"),
                "sections": [],
            }
        ]

    clean_pages = []
    for page in pages:
        sections = page.get("sections") or []
        clean_sections: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        # Guarantee navbar first
        if not sections or sections[0].get("type") != "navbar":
            sections = [{"type": "navbar", "variant": "sticky_glass", "id": "navbar"}] + list(sections)

        for idx, raw in enumerate(sections):
            if not isinstance(raw, dict):
                continue
            stype = raw.get("type")
            if stype not in SECTION_CATALOG:
                logger.info("LayoutPlan: dropping unknown section type %r", stype)
                continue
            variant = raw.get("variant")
            variants = SECTION_CATALOG[stype]
            if variant not in variants:
                variant = next(iter(variants))
            sid = raw.get("id") or f"{stype}_{idx}"
            if sid in seen_ids:
                sid = f"{sid}_{idx}"
            seen_ids.add(sid)
            clean_sections.append({**raw, "type": stype, "variant": variant, "id": sid})

        # Guarantee footer last
        if not clean_sections or clean_sections[-1].get("type") != "footer":
            clean_sections.append({"type": "footer", "variant": "multicol", "id": "footer"})

        clean_pages.append(
            {
                "slug": page.get("slug") or "index",
                "title": page.get("title") or project_spec.get("project_name", "Home"),
                "meta_title": page.get("meta_title") or page.get("title"),
                "meta_description": page.get("meta_description") or "",
                "lang": page.get("lang") or project_spec.get("lang", "it"),
                "sections": clean_sections,
            }
        )

    # Ensure every dynamic section declared in spec has a layout entry on the
    # home page; backfill `cms_key` so the assembly step can resolve CMS data
    # deterministically.
    dyn = project_spec.get("dynamic_sections") or []
    if dyn and clean_pages:
        home = clean_pages[0]
        existing_keys = {
            s.get("cms_key") for s in home["sections"]
            if s.get("type", "").startswith("dynamic_")
        }
        for d in dyn:
            if not isinstance(d, dict):
                continue
            key = (d.get("key") or d.get("name") or "").strip().lower() or None
            if not key or key in existing_keys:
                continue
            section_type, variant = _resolve_dynamic_variant(d)
            entry = {
                "type": section_type,
                "variant": variant,
                "id": key,
                "cms_key": key,
                "headline": d.get("label") or key.title(),
            }
            home["sections"].insert(-1, entry)
            existing_keys.add(key)

    return {"pages": clean_pages}


def _resolve_dynamic_variant(d: dict) -> tuple[str, str]:
    """Pick (section_type, variant) from a dynamic_section spec.

    Prefer the explicit `kind` (mapped via KIND_REGISTRY); fall back to a
    keyword guess on the legacy `name` field if `kind` is missing.
    """
    kind = (d.get("kind") or "").strip().lower()
    if kind and kind in KIND_REGISTRY:
        try:
            return section_template_for(kind)
        except Exception:
            pass
    return _guess_dynamic_variant(d.get("name") or d.get("key") or "")


def _guess_dynamic_variant(name: str) -> tuple[str, str]:
    n = (name or "").lower()
    if "menu" in n or "prodott" in n or "listino" in n:
        return "dynamic_menu", "cards"
    if "ora" in n or "hour" in n:
        return "dynamic_hours", "table"
    if "team" in n or "staff" in n:
        return "dynamic_team", "cards"
    if "faq" in n or "domand" in n:
        return "dynamic_faq", "accordion"
    if "galler" in n or "foto" in n or "gallery" in n:
        return "dynamic_gallery", "grid"
    if "test" in n or "review" in n or "recension" in n:
        return "dynamic_testimonials", "cards"
    if "service" in n or "servizi" in n:
        return "dynamic_services", "cards"
    if "pricing" in n or "prezzi" in n or "listino" in n:
        return "dynamic_pricing", "tiers"
    if "event" in n:
        return "dynamic_events", "cards"
    if "contact" in n or "contatt" in n:
        return "dynamic_contact", "list"
    return "dynamic_generic", "table"


async def run_layout_planner(
    project_spec: dict,
    site_copy: dict,
    design_tokens: dict | None = None,
    review_issues: list[dict] | None = None,
) -> dict:
    """Produce a LayoutPlan by consulting the LLM with the section catalog."""
    logger.info("Running layout planner")

    user_prompt = (
        "PROJECT SPEC:\n"
        f"{project_spec}\n\n"
        "SITE COPY (structured):\n"
        f"{site_copy}\n\n"
        "DESIGN TOKENS (for tone matching):\n"
        f"{design_tokens or {}}\n\n"
        "SECTION CATALOG (type:variant — description (fields)):\n"
        f"{catalog_summary()}\n"
    )
    if review_issues:
        issue_lines = []
        for i in review_issues[:12]:
            issue_lines.append(
                f"- [{i.get('severity', '?')}/{i.get('category', '?')}] {i.get('description', '')} → {i.get('suggested_fix', '')}"
            )
        user_prompt += (
            "\n\nPREVIOUS REVIEWER ISSUES TO ADDRESS IN THIS ITERATION:\n"
            + "\n".join(issue_lines)
            + "\n\nPick section variants that materially fix these issues (e.g. swap to minimal_centered if visual noise was flagged)."
        )

    try:
        plan = await call_llm(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            response_format="json",
            temperature=0.3,
            max_tokens=2048,
        )
    except Exception as exc:
        logger.warning("Layout planner LLM failed (%s); using default fallback plan", exc)
        plan = {}

    plan = _normalise_layout(plan if isinstance(plan, dict) else {}, project_spec)
    logger.info(
        "LayoutPlan: %d pages, %d total sections",
        len(plan["pages"]),
        sum(len(p["sections"]) for p in plan["pages"]),
    )
    return plan

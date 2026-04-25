"""Copy agent — generates rich, structured website copy from spec and research.

Output shape is consumed by the LayoutPlanner and the section Assembler. Every
section in the layout is keyed by its id under the top-level `sections` map, so
the builder can enrich its template context deterministically.
"""

from __future__ import annotations

import logging
from typing import Any

from app.services.llm_client import call_llm

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are an expert website copywriter and content strategist.

Given a project spec and research, output fully-structured website copy. Use Italian unless the spec sets another language. Write compelling, concrete, on-brand copy — no lorem ipsum, no placeholder-y text.

Output EXACT JSON:
{
  "meta": {
    "site_title": "string",
    "site_tagline": "string"
  },
  "contact": {
    "email": "string|null",
    "phone": "string|null",
    "address": "string|null",
    "hours": "string|null"
  },
  "pages": {
    "<page_slug>": {
      "title": "string",
      "meta_title": "string (50-60 chars)",
      "meta_description": "string (130-160 chars)"
    }
  },
  "sections": {
    "<section_id>": {
      "eyebrow": "string|null",
      "headline": "string",
      "subheadline": "string|null",
      "body": "string|null",
      "primary_cta": {"label": "string", "href": "string"}|null,
      "secondary_cta": {"label": "string", "href": "string"}|null,
      "image_query": "short english phrase for stock image search|null",
      "image_alt": "string|null",
      "items": [
        {
          "id": "string",
          "title": "string",
          "description": "string",
          "eyebrow": "string|null",
          "bullets": ["string", ...]|null,
          "link_href": "string|null",
          "link_label": "string|null",
          "image_query": "short english phrase|null",
          "icon_path": "string|null",
          "price": "string|null",
          "period": "string|null",
          "features": ["string", ...]|null,
          "featured": false,
          "cta_label": "string|null",
          "cta_href": "string|null",
          "name": "string|null",
          "role": "string|null",
          "bio": "string|null",
          "quote": "string|null",
          "author": "string|null",
          "rating": 5,
          "question": "string|null",
          "answer": "string|null"
        }
      ]|null
    }
  },
  "alt_texts": {"<image_id>": "alt text"}
}

Rules:
- Use predictable section ids: `hero`, `features`, `services`, `about`, `team`, `testimonials`, `pricing`, `faq`, `cta_primary`, `contact`, `gallery`, `footer`.
- For every image_query, write a SHORT English phrase (Unsplash/Pexels friendly): e.g. "modern italian restaurant interior", "confident business woman smiling".
- Fill `items` only for multi-item sections (features/services/team/testimonials/pricing/faq/gallery). Omit otherwise.
- For testimonials use `quote`, `author`, `role`. For faq use `question`, `answer`. For team use `name`, `role`, `bio`. For pricing use `name`, `price`, `features`, `cta_label`.
- Keep text tight; use sentence-case, no trailing periods on short headlines.
- Return ONLY valid JSON, no commentary.
"""


def _ensure_shape(data: Any) -> dict[str, Any]:
    out = data if isinstance(data, dict) else {}
    out.setdefault("meta", {})
    out.setdefault("contact", {})
    out.setdefault("pages", {})
    out.setdefault("sections", {})
    out.setdefault("alt_texts", {})
    return out


async def run_copy_agent(project_spec: dict, research: dict | None = None) -> dict:
    """Run the copy agent. Returns a structured site_copy dict."""
    logger.info("Running copy agent")

    user_prompt = f"PROJECT SPECIFICATION:\n{project_spec}"
    if research:
        user_prompt += f"\n\nRESEARCH FINDINGS:\n{research}"

    # Surface dynamic sections so the copywriter knows NOT to write items for
    # them — items live in the CMS and are populated by the customer. The copy
    # agent only provides the wrapping headline/subheadline.
    dyn = project_spec.get("dynamic_sections") or []
    if dyn:
        lines = []
        for d in dyn:
            if not isinstance(d, dict):
                continue
            key = d.get("key") or d.get("name") or "?"
            label = d.get("label") or "?"
            kind = d.get("kind") or "?"
            lines.append(f"  - id: {key}  label: {label}  kind: {kind}")
        user_prompt += (
            "\n\nDYNAMIC SECTIONS (managed by the customer in the CMS — only provide eyebrow/headline/subheadline keyed by the section id, no items):\n"
            + "\n".join(lines)
        )

    try:
        result = await call_llm(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            response_format="json",
            temperature=0.55,
            max_tokens=4096,
        )
    except Exception as exc:
        logger.exception("Copy agent failed: %s", exc)
        result = {}

    result = _ensure_shape(result)
    logger.info(
        "Copy agent produced %d sections across %d pages",
        len(result.get("sections", {})),
        len(result.get("pages", {})),
    )
    return result

"""Planner agent — analyzes brief and creates a project specification.

The planner now also identifies "dynamic sections" — content the client will
update on their own through the in-app CMS (no spreadsheets). Each dynamic
section is anchored to a typed `kind` from the CMS registry so the rest of the
pipeline knows which template to render and which fields to seed.
"""

import logging

from app.cms import KIND_REGISTRY
from app.services.llm_client import call_llm

logger = logging.getLogger(__name__)


def _kinds_catalog_for_prompt() -> str:
    lines = []
    for k, v in KIND_REGISTRY.items():
        if k == "generic":
            continue
        lines.append(f"  - {k}: {v['label']} — {v['description']}")
    lines.append("  - generic: Sezione personalizzata — fallback per contenuti che non rientrano negli altri tipi.")
    return "\n".join(lines)


SYSTEM_PROMPT = f"""You are a senior web project planner. Given a website brief, produce a detailed project specification as JSON.

Output format:
{{
  "project_name": "string",
  "target_audience": "string",
  "site_type": "landing|multi-page|portfolio|ecommerce|blog|restaurant|local_business",
  "pages": [
    {{
      "name": "string",
      "slug": "string",
      "purpose": "string",
      "sections": ["hero", "features", "testimonials", ...]
    }}
  ],
  "design_direction": "string describing visual style",
  "tone": "professional|casual|luxury|minimal|bold",
  "key_sections": ["list of critical sections"],
  "technical_requirements": ["responsive", "fast loading", ...],
  "acceptance_criteria": ["measurable criteria for done"],
  "dynamic_sections": []
}}

The "dynamic_sections" field lists every collection the client must be able to
edit autonomously through the in-app CMS (NOT through code or spreadsheets).
Available kinds:
{_kinds_catalog_for_prompt()}

For each dynamic section emit:
{{
  "kind": "menu",                    // MUST be one of the kinds above
  "key": "menu",                     // lowercase slug, unique within the site (e.g. "menu", "orari", "team")
  "label": "Menu di stagione",       // Italian label shown in the admin dashboard
  "seed_examples": true              // true to bootstrap with realistic placeholder items
}}

Guidelines:
- A restaurant always needs at least `menu` and `hours`. A consultant a `services` and `faq`. An events space `events`. A hotel `gallery` + `pricing`. Use judgement.
- Never duplicate a key. Prefer concise, descriptive keys.
- Set `seed_examples: true` so the customer sees realistic example content immediately and can edit it.
- Leave `dynamic_sections` as `[]` ONLY for purely static one-pagers with no recurring updates.

Be specific and actionable. Prefer clean, modern designs. Default to a single-page landing if unclear."""


async def run_planner(brief: str, config: dict | None = None) -> dict:
    """Run the planner agent on a brief. Returns project_spec."""
    logger.info("Running planner agent")

    user_prompt = f"Website brief:\n{brief}"
    if config:
        user_prompt += f"\n\nAdditional configuration:\n{config}"

    uploaded_assets = (config or {}).get("uploaded_assets", [])
    if uploaded_assets:
        asset_lines = []
        for a in uploaded_assets:
            label = a.get("asset_type", "reference").upper()
            desc = a.get("description", "") or a.get("filename", "")
            asset_lines.append(f"  - [{label}] {desc}")
        user_prompt += (
            "\n\nUploaded assets provided by the client:\n"
            + "\n".join(asset_lines)
            + "\nFactor these into your design direction and project spec."
        )

    result = await call_llm(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        response_format="json",
        temperature=0.4,
        max_tokens=2048,
    )

    result["dynamic_sections"] = _normalize_dynamic_sections(result.get("dynamic_sections"))
    logger.info(
        "Planner output: %s (%d dynamic sections)",
        result.get("project_name", "unknown"),
        len(result["dynamic_sections"]),
    )
    return result


_LEGACY_NAME_TO_KIND = {
    "menu": "menu",
    "orari": "hours",
    "hours": "hours",
    "faq": "faq",
    "team": "team",
    "staff": "team",
    "gallery": "gallery",
    "galleria": "gallery",
    "testimonials": "testimonials",
    "testimonianze": "testimonials",
    "services": "services",
    "servizi": "services",
    "pricing": "pricing",
    "prezzi": "pricing",
    "listino": "pricing",
    "products": "products",
    "prodotti": "products",
    "events": "events",
    "eventi": "events",
    "contact": "contact_info",
    "contatti": "contact_info",
}


def _normalize_dynamic_sections(raw: object) -> list[dict]:
    """Coerce the LLM's dynamic_sections list into a clean, validated form.

    Tolerates the legacy `name/columns/description` shape (older cached
    prompts) and falls back to the `generic` kind if the LLM names something
    we don't have.
    """
    if not isinstance(raw, list):
        return []

    out: list[dict] = []
    used_keys: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        kind = (entry.get("kind") or "").strip().lower() or None
        if not kind:
            legacy = (entry.get("name") or "").strip().lower()
            for token, mapped in _LEGACY_NAME_TO_KIND.items():
                if token in legacy:
                    kind = mapped
                    break
        if not kind or kind not in KIND_REGISTRY:
            kind = "generic"

        key = (entry.get("key") or entry.get("name") or kind).strip().lower()
        key = "".join(c if c.isalnum() else "-" for c in key).strip("-") or kind
        base = key
        n = 2
        while key in used_keys:
            key = f"{base}-{n}"
            n += 1
        used_keys.add(key)

        label = (entry.get("label") or "").strip() or KIND_REGISTRY[kind]["default_label"]
        seed = entry.get("seed_examples")
        if seed is None:
            seed = True

        out.append({
            "kind": kind,
            "key": key,
            "label": label,
            "seed_examples": bool(seed),
        })

    return out

"""Planner agent — analyzes brief and creates a project specification."""

import logging
from app.services.llm_client import call_llm

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a senior web project planner. Given a website brief, produce a detailed project specification as JSON.

Output format:
{
  "project_name": "string",
  "target_audience": "string",
  "site_type": "landing|multi-page|portfolio|ecommerce|blog",
  "pages": [
    {
      "name": "string",
      "slug": "string",
      "purpose": "string",
      "sections": ["hero", "features", "testimonials", ...]
    }
  ],
  "design_direction": "string describing visual style",
  "tone": "professional|casual|luxury|minimal|bold",
  "key_sections": ["list of critical sections"],
  "technical_requirements": ["responsive", "fast loading", ...],
  "acceptance_criteria": ["measurable criteria for done"],
  "dynamic_sections": []
}

The "dynamic_sections" field must list any sections whose content should be editable by the client without touching code (menus, prices, opening hours, team members, events, FAQ, etc.).
For each dynamic section include:
{
  "name": "slug_identifier",         // lowercase, underscores, e.g. "menu", "orari", "team"
  "label": "Human label in Italian", // e.g. "Menu", "Orari di apertura", "Staff"
  "columns": ["Col1", "Col2", ...],  // spreadsheet column headers appropriate for this data
  "description": "What this section contains"
}
Leave dynamic_sections as [] only for purely static sites with no regularly-updated data.

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

    logger.info(f"Planner output: {result.get('project_name', 'unknown')}")
    return result

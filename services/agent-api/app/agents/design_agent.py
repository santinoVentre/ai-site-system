"""Design agent — emits design tokens consumable by Tailwind + CSS variables.

Output is rich enough for the base template to:
  1. Inject a Tailwind config fragment (brand 50-900 scale, fontFamily extensions).
  2. Expose CSS custom properties for non-Tailwind surfaces.
  3. Pre-wire Google Fonts via URL params.
"""

from __future__ import annotations

import logging
from typing import Any

from app.services.llm_client import call_llm

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are a senior visual designer. Given a project spec, produce a cohesive design token system for a Tailwind CSS + Alpine.js website.

Output EXACT JSON:
{
  "colors": {
    "primary":         "#RRGGBB",
    "secondary":       "#RRGGBB",
    "accent":          "#RRGGBB",
    "background":      "#RRGGBB",
    "surface":         "#RRGGBB",
    "text_primary":    "#RRGGBB",
    "text_secondary":  "#RRGGBB",
    "border":          "#RRGGBB",
    "success":         "#RRGGBB",
    "error":           "#RRGGBB",
    "brand_50":        "#RRGGBB",
    "brand_100":       "#RRGGBB",
    "brand_200":       "#RRGGBB",
    "brand_300":       "#RRGGBB",
    "brand_400":       "#RRGGBB",
    "brand_500":       "#RRGGBB",
    "brand_600":       "#RRGGBB",
    "brand_700":       "#RRGGBB",
    "brand_800":       "#RRGGBB",
    "brand_900":       "#RRGGBB"
  },
  "typography": {
    "headings_font":       "Google font family name (e.g. 'Playfair Display')",
    "headings_font_param": "URL-encoded weights (e.g. 'Playfair+Display:wght@600;700;800')",
    "body_font":           "Google font family name (e.g. 'Inter')",
    "body_font_param":     "URL-encoded weights (e.g. 'Inter:wght@400;500;600')",
    "base_size": "16px",
    "scale_ratio": 1.25
  },
  "spacing": {
    "section_padding_y": "80px",
    "container_padding_x": "24px"
  },
  "layout": {
    "max_width": "1200px",
    "breakpoints": {"mobile":"480px","tablet":"768px","desktop":"1024px","wide":"1440px"}
  },
  "component_styles": {
    "buttons": {"border_radius": "12px"},
    "cards":   {"border_radius": "16px", "shadow": "0 2px 8px rgba(0,0,0,0.06)"},
    "inputs":  {"border_radius": "10px"}
  }
}

Rules:
- The `brand_50 … brand_900` scale MUST be a monotone, perceptually-even tint ramp of the primary color, suitable for Tailwind utility classes (bg-brand-50, text-brand-700, ring-brand-500). Lightest at 50, darkest at 900.
- The primary/secondary/accent MUST be inside the brand ramp (primary≈brand_600, secondary≈brand_800, accent≈a complementary hue).
- `text_primary` should be nearly-black (never pure #000). `background` should be off-white or very light. `surface` a very soft tint (#f5f7fb kind).
- Fonts MUST be real Google Fonts. Pair one display/serif or geometric heading with a neutral sans body (e.g. Playfair Display + Inter, Fraunces + Manrope, Space Grotesk + IBM Plex Sans).
- The `*_font_param` fields must be valid Google Fonts URL parameter values (spaces → +, weights via :wght@x;y;z). Do NOT include the family= prefix.
- Return ONLY JSON, no commentary.
"""


def _fallback_tokens() -> dict[str, Any]:
    return {
        "colors": {
            "primary": "#3a5afe",
            "secondary": "#1e2a80",
            "accent": "#f59e0b",
            "background": "#ffffff",
            "surface": "#f5f7fb",
            "text_primary": "#0f172a",
            "text_secondary": "#475569",
            "border": "#e2e8f0",
            "success": "#10b981",
            "error": "#ef4444",
            "brand_50": "#f5f7ff",
            "brand_100": "#e4ebff",
            "brand_200": "#c1cfff",
            "brand_300": "#94a9ff",
            "brand_400": "#6b83ff",
            "brand_500": "#4c66ff",
            "brand_600": "#3a5afe",
            "brand_700": "#2f47cc",
            "brand_800": "#24358f",
            "brand_900": "#172360",
        },
        "typography": {
            "headings_font": "Inter",
            "headings_font_param": "Inter:wght@600;700;800",
            "body_font": "Inter",
            "body_font_param": "Inter:wght@400;500;600",
            "base_size": "16px",
            "scale_ratio": 1.25,
        },
        "spacing": {"section_padding_y": "80px", "container_padding_x": "24px"},
        "layout": {
            "max_width": "1200px",
            "breakpoints": {"mobile": "480px", "tablet": "768px", "desktop": "1024px", "wide": "1440px"},
        },
        "component_styles": {
            "buttons": {"border_radius": "12px"},
            "cards": {"border_radius": "16px", "shadow": "0 2px 8px rgba(0,0,0,0.06)"},
            "inputs": {"border_radius": "10px"},
        },
    }


def _ensure_shape(tokens: Any) -> dict[str, Any]:
    base = _fallback_tokens()
    if not isinstance(tokens, dict):
        return base
    merged = base
    merged["colors"] = {**base["colors"], **(tokens.get("colors") or {})}
    merged["typography"] = {**base["typography"], **(tokens.get("typography") or {})}
    merged["spacing"] = {**base["spacing"], **(tokens.get("spacing") or {})}
    merged["layout"] = {**base["layout"], **(tokens.get("layout") or {})}
    merged["component_styles"] = {**base["component_styles"], **(tokens.get("component_styles") or {})}
    return merged


async def run_design_agent(
    project_spec: dict,
    research: dict | None = None,
    uploaded_assets: list[dict] | None = None,
) -> dict:
    """Run the design agent. Returns design_tokens."""
    logger.info("Running design agent")

    user_prompt = f"PROJECT SPECIFICATION:\n{project_spec}"
    if research:
        user_prompt += f"\n\nRESEARCH:\n{research}"
    if uploaded_assets:
        logos = [a for a in uploaded_assets if a.get("asset_type") == "logo"]
        refs = [a for a in uploaded_assets if a.get("asset_type") != "logo"]
        if logos:
            names = ", ".join(a.get("description") or a.get("filename", "") for a in logos)
            user_prompt += (
                f"\n\nCLIENT LOGOS: {names}. Ensure the primary/secondary colors would look natural alongside the logo."
            )
        if refs:
            descs = "; ".join(a.get("description") or a.get("filename", "") for a in refs)
            user_prompt += f"\n\nDESIGN REFERENCES: {descs}. Let them influence typography/colors."

    try:
        result = await call_llm(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            response_format="json",
            temperature=0.4,
            max_tokens=2048,
        )
    except Exception as exc:
        logger.exception("Design agent failed: %s", exc)
        result = {}

    tokens = _ensure_shape(result)
    logger.info(
        "Design tokens ready — primary=%s, headings=%s",
        tokens["colors"].get("primary"),
        tokens["typography"].get("headings_font"),
    )
    return tokens

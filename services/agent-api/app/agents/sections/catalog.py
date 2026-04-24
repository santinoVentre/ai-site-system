"""Section catalog — maps section types/variants to Jinja templates with schemas."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "templates"

_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"], default_for_string=False),
    trim_blocks=True,
    lstrip_blocks=True,
)


# Catalog: section_type -> {variant_name: {template: ..., description: ..., fields: [...]}, ...}
# "fields" is the data contract the LLM (or the code) must fill in the copy artifact.
SECTION_CATALOG: dict[str, dict[str, dict[str, Any]]] = {
    "navbar": {
        "sticky_glass": {
            "template": "navbar/sticky_glass.html.j2",
            "description": "Sticky translucent navbar with mobile Alpine menu and CTA button.",
            "fields": ["brand_name", "cta_label", "cta_href"],
        },
        "solid_centered": {
            "template": "navbar/solid_centered.html.j2",
            "description": "Solid coloured navbar with logo centred and symmetrical menu.",
            "fields": ["brand_name", "cta_label", "cta_href"],
        },
    },
    "hero": {
        "split_image": {
            "template": "hero/split_image.html.j2",
            "description": "Split layout with headline on the left and a hero image on the right.",
            "fields": ["eyebrow", "headline", "subheadline", "primary_cta", "secondary_cta", "image_query"],
        },
        "fullbleed_overlay": {
            "template": "hero/fullbleed_overlay.html.j2",
            "description": "Full-bleed background image with dark overlay and centred headline.",
            "fields": ["eyebrow", "headline", "subheadline", "primary_cta", "secondary_cta", "image_query"],
        },
        "minimal_centered": {
            "template": "hero/minimal_centered.html.j2",
            "description": "Minimal typographic hero without imagery, ideal for services/B2B.",
            "fields": ["eyebrow", "headline", "subheadline", "primary_cta", "secondary_cta"],
        },
    },
    "features": {
        "grid3": {
            "template": "features/grid3.html.j2",
            "description": "Three-column icon+title+copy grid for key features/services.",
            "fields": ["title", "subtitle", "items"],
        },
        "alternating": {
            "template": "features/alternating.html.j2",
            "description": "Alternating image/text rows — great for storytelling.",
            "fields": ["title", "subtitle", "items"],
        },
    },
    "testimonials": {
        "cards": {
            "template": "testimonials/cards.html.j2",
            "description": "Grid of customer quote cards with avatar, name, role.",
            "fields": ["title", "subtitle", "items"],
        },
        "marquee": {
            "template": "testimonials/marquee.html.j2",
            "description": "Infinite horizontal marquee of testimonial cards.",
            "fields": ["title", "items"],
        },
    },
    "pricing": {
        "tiers": {
            "template": "pricing/tiers.html.j2",
            "description": "Three-tier pricing cards with feature lists.",
            "fields": ["title", "subtitle", "items"],
        },
    },
    "team": {
        "grid": {
            "template": "team/grid.html.j2",
            "description": "Team grid with headshot, name, role, and optional socials.",
            "fields": ["title", "subtitle", "items"],
        },
    },
    "faq": {
        "accordion": {
            "template": "faq/accordion.html.j2",
            "description": "Accessible accordion with Alpine state and keyboard support.",
            "fields": ["title", "subtitle", "items"],
        },
    },
    "cta": {
        "banner": {
            "template": "cta/banner.html.j2",
            "description": "Full-width CTA banner with heading and single button.",
            "fields": ["headline", "subheadline", "primary_cta"],
        },
        "split_cards": {
            "template": "cta/split_cards.html.j2",
            "description": "Two-card CTA block for 'contact' vs 'explore'.",
            "fields": ["headline", "subheadline", "cards"],
        },
    },
    "contact": {
        "form_info": {
            "template": "contact/form_info.html.j2",
            "description": "Contact form + company info/map side-by-side.",
            "fields": ["title", "subtitle", "address", "phone", "email", "hours", "map_embed_url"],
        },
    },
    "footer": {
        "multicol": {
            "template": "footer/multicol.html.j2",
            "description": "Multi-column footer with brand, nav columns, socials.",
            "fields": ["brand_tagline", "columns", "social"],
        },
    },
    "gallery": {
        "grid": {
            "template": "gallery/grid.html.j2",
            "description": "Responsive image grid with lightbox (Alpine).",
            "fields": ["title", "subtitle", "items"],
        },
    },
    # Dynamic sections — render a placeholder and hydrate from Sheets at runtime
    "dynamic_menu": {
        "cards": {
            "template": "dynamic_menu/cards.html.j2",
            "description": "Restaurant/product menu loaded from Sheets; server renders graceful fallback.",
            "fields": ["title", "subtitle", "sheet_key"],
        },
    },
    "dynamic_hours": {
        "table": {
            "template": "dynamic_hours/table.html.j2",
            "description": "Opening hours key-value table from Sheets.",
            "fields": ["title", "subtitle", "sheet_key"],
        },
    },
    "dynamic_team": {
        "cards": {
            "template": "dynamic_team/cards.html.j2",
            "description": "Team members loaded from Sheets.",
            "fields": ["title", "subtitle", "sheet_key"],
        },
    },
    "dynamic_faq": {
        "accordion": {
            "template": "dynamic_faq/accordion.html.j2",
            "description": "FAQ accordion loaded from Sheets.",
            "fields": ["title", "subtitle", "sheet_key"],
        },
    },
    "dynamic_gallery": {
        "grid": {
            "template": "dynamic_gallery/grid.html.j2",
            "description": "Image gallery loaded from Sheets.",
            "fields": ["title", "subtitle", "sheet_key"],
        },
    },
    "dynamic_generic": {
        "table": {
            "template": "dynamic_generic/table.html.j2",
            "description": "Fallback table renderer for any Sheets-backed section.",
            "fields": ["title", "subtitle", "sheet_key"],
        },
    },
}


def available_variants(section_type: str) -> list[str]:
    return list(SECTION_CATALOG.get(section_type, {}).keys())


def catalog_summary() -> str:
    """Short, LLM-friendly description of the catalog for the layout planner."""
    lines = []
    for stype, variants in SECTION_CATALOG.items():
        for vname, meta in variants.items():
            lines.append(f"- {stype}:{vname} — {meta['description']} (fields: {', '.join(meta['fields'])})")
    return "\n".join(lines)


def render_section(section_type: str, variant: str, ctx: dict[str, Any]) -> str:
    """Render a single section template with the given context. Raises KeyError if not found."""
    try:
        meta = SECTION_CATALOG[section_type][variant]
    except KeyError:
        raise KeyError(f"Unknown section {section_type}:{variant}")
    template = _env.get_template(meta["template"])
    return template.render(**ctx)


def render_page(layout: list[dict], ctx: dict[str, Any]) -> str:
    """Render a page by concatenating its section layout inside the base HTML template."""
    sections_html = []
    for entry in layout:
        stype = entry.get("type")
        # Custom / inline HTML pass-through (from LLM fallback for bespoke sections)
        if entry.get("inline_html"):
            sections_html.append(entry["inline_html"])
            continue
        if stype not in SECTION_CATALOG:
            logger.warning(f"Unknown section type {stype!r}; skipping")
            continue
        variant = entry.get("variant") or (available_variants(stype)[0] if available_variants(stype) else None)
        if not variant:
            logger.warning(f"No variant available for section type {stype}; skipping")
            continue
        section_ctx = {
            **ctx,
            "section": {**entry, "type": stype, "variant": variant},
        }
        try:
            html = render_section(stype, variant, section_ctx)
        except Exception as exc:
            logger.exception(f"Failed to render {stype}:{variant}: {exc}")
            html = f"<!-- failed to render {stype}:{variant}: {exc} -->"
        sections_html.append(html)

    page_template = _env.get_template("base/page.html.j2")
    return page_template.render(
        sections_html="\n".join(sections_html),
        **ctx,
    )

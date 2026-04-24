"""Section component library — Tailwind CSS + Alpine.js templates.

Each section template accepts a consistent data contract:
    - section: dict with the section's own id, variant, and fields (title, copy, items, etc.)
    - design: design tokens (colors, fonts, radii) and Tailwind config snippet
    - page: dict with page-level context (title, slug, lang, pages list for navbar)
    - images: image URL map keyed by section id
    - sheets_url: URL to fetch dynamic Sheets data (or None)
    - brand: dict with brand info (name, logo_url, social)
"""

from .catalog import SECTION_CATALOG, render_section, render_page, available_variants
from .assembly import assemble_site

__all__ = [
    "SECTION_CATALOG",
    "render_section",
    "render_page",
    "available_variants",
    "assemble_site",
]

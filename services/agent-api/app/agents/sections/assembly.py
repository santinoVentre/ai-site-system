"""Assemble a full multi-page site from a layout plan + design tokens + copy + images.

Called by the builder after the LayoutPlan has been produced. Pure Python — no LLM
calls here: the templates deterministically turn structured data into HTML.
"""

from __future__ import annotations

import datetime as _dt
import logging
import re
from typing import Any

from .catalog import SECTION_CATALOG, available_variants, render_page

logger = logging.getLogger(__name__)


def _slugify(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "page"


def _build_nav_items(pages: list[dict]) -> list[dict[str, str]]:
    items = []
    for p in pages:
        slug = p.get("slug") or _slugify(p.get("title", "") or p.get("id", ""))
        href = "./index.html" if slug in ("home", "index", "") else f"./{slug}.html"
        items.append({"label": p.get("nav_label") or p.get("title") or slug, "href": href})
    return items


def _structured_data(brand: dict, contact: dict, site_type: str) -> dict:
    base = {
        "@context": "https://schema.org",
        "name": brand.get("name", ""),
        "url": brand.get("home_href", ""),
    }
    if site_type == "restaurant":
        base["@type"] = "Restaurant"
    elif site_type == "local_business":
        base["@type"] = "LocalBusiness"
    else:
        base["@type"] = "Organization"
    if brand.get("logo_url"):
        base["logo"] = brand["logo_url"]
    if contact.get("phone"):
        base["telephone"] = contact["phone"]
    if contact.get("email"):
        base["email"] = contact["email"]
    if contact.get("address"):
        base["address"] = {"@type": "PostalAddress", "streetAddress": contact["address"]}
    return base


def _coerce_image_url(
    image_urls: dict[str, str],
    section_id: str | None,
    image_query: str | None,
) -> str | None:
    if not image_urls:
        return None
    if section_id and section_id in image_urls:
        return image_urls[section_id]
    if image_query and image_query in image_urls:
        return image_urls[image_query]
    return None


def _enrich_section(
    section: dict,
    *,
    copy_payload: dict,
    image_urls: dict[str, str],
    cms_data: dict[str, dict] | None = None,
) -> dict:
    """Merge copy data for this section id into the section dict (if any) and resolve images.

    If the section is dynamic (`type` starts with `dynamic_`) and matching CMS
    data is present, attach the CMS items + settings so the Jinja template can
    prerender them server-side (great for SEO).
    """
    enriched = dict(section)
    sid = enriched.get("id")
    if sid:
        section_copy = (copy_payload.get("sections") or {}).get(sid) or {}
        for k, v in section_copy.items():
            enriched.setdefault(k, v)

    image_url = _coerce_image_url(image_urls, sid, enriched.get("image_query"))
    if image_url and not enriched.get("image_url"):
        enriched["image_url"] = image_url

    for item in enriched.get("items") or []:
        if not isinstance(item, dict):
            continue
        iq = item.get("image_query") or item.get("id")
        if iq and not item.get("image_url"):
            resolved = _coerce_image_url(image_urls, iq, iq)
            if resolved:
                item["image_url"] = resolved

    if cms_data and (enriched.get("type") or "").startswith("dynamic_"):
        cms_key = enriched.get("cms_key") or sid
        if cms_key and cms_key in cms_data:
            block = cms_data[cms_key]
            if isinstance(block, dict):
                enriched.setdefault("cms_items", block.get("items") or [])
                cms_settings = block.get("settings") or {}
                for k, v in cms_settings.items():
                    if v not in (None, "") and not enriched.get(k):
                        enriched[k] = v
                enriched.setdefault("cms_kind", block.get("kind"))
                enriched.setdefault("cms_label", block.get("label"))
            elif isinstance(block, list):
                enriched.setdefault("cms_items", block)

    return enriched


def assemble_site(
    *,
    project_spec: dict,
    site_copy: dict,
    design_tokens: dict,
    layout_plan: dict,
    image_urls: dict[str, str] | None = None,
    cms_data: dict[str, dict] | None = None,
    cms_data_url: str | None = None,
) -> dict[str, str]:
    """Return a mapping of file path -> file contents.

    layout_plan schema (simplified):
    {
      "pages": [
        {
          "slug": "index",
          "title": "Home",
          "meta_title": "...",
          "meta_description": "...",
          "sections": [
             {"type": "navbar",   "variant": "sticky_glass", "id": "navbar"},
             {"type": "hero",     "variant": "split_image",  "id": "hero"},
             ...
          ]
        }, ...
      ]
    }
    """
    image_urls = image_urls or {}
    cms_data = cms_data or {}
    files: dict[str, str] = {}

    brand = {
        "name": project_spec.get("brand_name") or project_spec.get("name") or "Brand",
        "logo_url": (project_spec.get("uploaded_assets") or {}).get("logo_url"),
        "home_href": "./index.html",
    }
    contact = {
        "email": project_spec.get("contact_email") or (site_copy.get("contact") or {}).get("email"),
        "phone": project_spec.get("contact_phone") or (site_copy.get("contact") or {}).get("phone"),
        "address": project_spec.get("address") or (site_copy.get("contact") or {}).get("address"),
        "hours": (site_copy.get("contact") or {}).get("hours"),
    }
    site_type = project_spec.get("site_type") or "organization"

    pages = layout_plan.get("pages") or []
    nav_items = _build_nav_items(pages)

    structured = _structured_data(brand, contact, site_type)

    for page in pages:
        slug = page.get("slug") or _slugify(page.get("title", "page"))
        filename = "index.html" if slug in ("home", "index", "") else f"{slug}.html"

        page_ctx_meta = {
            "title": page.get("title") or brand["name"],
            "meta_title": page.get("meta_title") or page.get("title") or brand["name"],
            "meta_description": page.get("meta_description") or "",
            "canonical": page.get("canonical") or "",
            "og_image": page.get("og_image"),
            "og_type": page.get("og_type", "website"),
            "lang": page.get("lang", project_spec.get("lang", "it")),
            "structured_data": structured,
        }

        sections = []
        for raw in page.get("sections") or []:
            stype = raw.get("type")
            if not stype or stype not in SECTION_CATALOG:
                logger.warning("Unknown section type %s — skipping", stype)
                continue
            variant = raw.get("variant")
            if not variant or variant not in SECTION_CATALOG[stype]:
                fallback = available_variants(stype)[0]
                logger.info("Using fallback variant %s for %s", fallback, stype)
                variant = fallback
            section = _enrich_section(
                {**raw, "variant": variant},
                copy_payload=site_copy,
                image_urls=image_urls,
                cms_data=cms_data,
            )
            sections.append(section)

        ctx = {
            "brand": brand,
            "contact": contact,
            "design": design_tokens,
            "page": page_ctx_meta,
            "nav_items": nav_items,
            "site_copy": site_copy,
            "cms_data": cms_data,
            "cms_data_url": cms_data_url,
            "current_year": _dt.datetime.now().year,
        }

        html = render_page(sections, ctx)
        files[filename] = html

    files["robots.txt"] = _render_robots()
    files["sitemap.xml"] = _render_sitemap(pages, brand.get("home_href", ""))
    return files


def _render_robots() -> str:
    return "User-agent: *\nAllow: /\nSitemap: sitemap.xml\n"


def _render_sitemap(pages: list[dict], base_href: str) -> str:
    today = _dt.date.today().isoformat()
    base = base_href.rstrip("/") if base_href else ""
    entries = []
    for page in pages:
        slug = page.get("slug") or _slugify(page.get("title", "page"))
        path = "" if slug in ("home", "index", "") else f"{slug}.html"
        loc = f"{base}/{path}" if base else path or "index.html"
        entries.append(
            f"  <url>\n    <loc>{loc}</loc>\n    <lastmod>{today}</lastmod>\n  </url>"
        )
    body = "\n".join(entries)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{body}\n"
        "</urlset>\n"
    )

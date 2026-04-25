"""Custom CMS — tipi di contenuto dinamico modificabili dal cliente.

Self-hosted, typed, image-aware content store editable from the admin
dashboard. Each "kind" (menu, hours, faq, ...) defines a structured schema
the API validates against and that the renderer/admin UI consume.
"""

from .kinds import (
    KIND_REGISTRY,
    available_kinds,
    get_kind,
    section_template_for,
    validate_item_data,
    validate_section_settings,
)

__all__ = [
    "KIND_REGISTRY",
    "available_kinds",
    "get_kind",
    "section_template_for",
    "validate_item_data",
    "validate_section_settings",
]

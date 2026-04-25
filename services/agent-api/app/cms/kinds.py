"""CMS kinds registry — schema tipizzato per ogni tipo di contenuto dinamico.

A "kind" describes one editable content collection (e.g. menu, opening hours,
FAQ). It holds:
- a human-readable label (Italian, so the dashboard speaks the customer's
  language)
- a list of fields (data contract for each item)
- example items used as seeds when the agent first creates the section
- helpers used by the public renderer to format prerendered HTML

The same registry is consumed by:
- the admin UI (to render the form fields)
- the API (to validate item payloads)
- the assembly step (to prerender HTML before client-side hydration)
- the agents (to know what to seed when the brief mentions e.g. a menu)
"""

from __future__ import annotations

from typing import Any, Iterable

# ---- Field type catalog -------------------------------------------------------

ALLOWED_FIELD_TYPES = {
    "text",        # short text input
    "textarea",    # multi-line text
    "richtext",    # multi-line text rendered preserving line breaks
    "number",      # numeric input
    "price",       # numeric with currency formatting (renders e.g. "12,00 €")
    "image",       # references an uploaded ContentImage URL
    "url",         # http(s) URL
    "email",       # email address
    "tel",         # phone number
    "time",        # HH:MM
    "date",        # YYYY-MM-DD
    "select",      # one of `options`
    "multiselect", # subset of `options`
    "list",        # list of short text items (e.g. pricing features)
    "boolean",     # checkbox
}


def _field(
    key: str,
    label: str,
    type: str = "text",
    *,
    required: bool = False,
    placeholder: str | None = None,
    help: str | None = None,
    options: list[dict[str, str]] | None = None,
    rows: int | None = None,
) -> dict[str, Any]:
    if type not in ALLOWED_FIELD_TYPES:
        raise ValueError(f"Unknown CMS field type: {type}")
    field: dict[str, Any] = {
        "key": key,
        "label": label,
        "type": type,
        "required": required,
    }
    if placeholder:
        field["placeholder"] = placeholder
    if help:
        field["help"] = help
    if options is not None:
        field["options"] = options
    if rows is not None:
        field["rows"] = rows
    return field


def _opt(value: str, label: str) -> dict[str, str]:
    return {"value": value, "label": label}


# ---- Kinds registry -----------------------------------------------------------

# Each kind:
#   - label / description: shown in admin UI
#   - icon: emoji used in cards
#   - default_label: suggested section label when created
#   - section_template: which catalog variant the assembly step should use
#       (kind -> "<section_type>:<variant>")
#   - fields: ordered list, drives the form
#   - item_label: builder of a short summary for each item (used in admin lists)
#   - examples: seed items the agent can drop when the section is created
#   - settings: optional section-level fields (eyebrow, headline, subheadline)

_DEFAULT_SECTION_SETTINGS_FIELDS = [
    _field("eyebrow", "Eyebrow", "text", placeholder="Es. Il nostro"),
    _field("headline", "Titolo", "text", placeholder="Es. Il nostro menu"),
    _field("subheadline", "Sottotitolo", "textarea", rows=2),
]


KIND_REGISTRY: dict[str, dict[str, Any]] = {
    # ------------------------------------------------------------- Menu
    "menu": {
        "label": "Menu",
        "icon": "🍝",
        "description": "Voci di menu organizzate per categoria, con prezzo e foto.",
        "default_label": "Menu",
        "section_template": "dynamic_menu:cards",
        "fields": [
            _field("name", "Nome piatto", "text", required=True, placeholder="Es. Tagliatelle al ragù"),
            _field("description", "Descrizione", "textarea", rows=2),
            _field("category", "Categoria", "text", placeholder="Es. Antipasti, Primi, Dolci"),
            _field("price", "Prezzo", "price"),
            _field("image", "Foto", "image"),
            _field("available", "Disponibile", "boolean"),
        ],
        "item_label": lambda d: d.get("name") or "Voce senza nome",
        "examples": [
            {"name": "Antipasto della casa", "category": "Antipasti", "price": "12.00",
             "description": "Selezione di salumi e formaggi locali.", "available": True},
            {"name": "Tagliatelle al ragù", "category": "Primi", "price": "14.00",
             "description": "Tagliatelle fresche al ragù di carne.", "available": True},
            {"name": "Tiramisù", "category": "Dolci", "price": "6.50",
             "description": "Ricetta tradizionale.", "available": True},
        ],
        "settings_fields": _DEFAULT_SECTION_SETTINGS_FIELDS,
    },

    # ------------------------------------------------------------- Hours
    "hours": {
        "label": "Orari di apertura",
        "icon": "🕒",
        "description": "Giorni della settimana con orari di apertura e chiusura.",
        "default_label": "Orari",
        "section_template": "dynamic_hours:table",
        "fields": [
            _field("day", "Giorno", "select", required=True, options=[
                _opt("Lunedì", "Lunedì"),
                _opt("Martedì", "Martedì"),
                _opt("Mercoledì", "Mercoledì"),
                _opt("Giovedì", "Giovedì"),
                _opt("Venerdì", "Venerdì"),
                _opt("Sabato", "Sabato"),
                _opt("Domenica", "Domenica"),
            ]),
            _field("open", "Apertura", "time", placeholder="09:00"),
            _field("close", "Chiusura", "time", placeholder="20:00"),
            _field("note", "Note", "text", placeholder="Es. Chiuso, su prenotazione"),
        ],
        "item_label": lambda d: d.get("day") or "Giorno",
        "examples": [
            {"day": "Lunedì", "open": "", "close": "", "note": "Chiuso"},
            {"day": "Martedì", "open": "12:00", "close": "23:00"},
            {"day": "Mercoledì", "open": "12:00", "close": "23:00"},
            {"day": "Giovedì", "open": "12:00", "close": "23:00"},
            {"day": "Venerdì", "open": "12:00", "close": "23:30"},
            {"day": "Sabato", "open": "12:00", "close": "23:30"},
            {"day": "Domenica", "open": "12:00", "close": "16:00"},
        ],
        "settings_fields": _DEFAULT_SECTION_SETTINGS_FIELDS,
    },

    # ------------------------------------------------------------- FAQ
    "faq": {
        "label": "Domande frequenti",
        "icon": "❓",
        "description": "Domande e risposte mostrate in un accordion.",
        "default_label": "FAQ",
        "section_template": "dynamic_faq:accordion",
        "fields": [
            _field("question", "Domanda", "text", required=True),
            _field("answer", "Risposta", "richtext", required=True, rows=4),
            _field("category", "Categoria", "text"),
        ],
        "item_label": lambda d: d.get("question") or "Domanda",
        "examples": [
            {"question": "Come posso prenotare?", "answer": "Puoi prenotare chiamandoci o scrivendoci via WhatsApp."},
            {"question": "Accettate carte?", "answer": "Sì, accettiamo tutte le principali carte di credito e debito."},
        ],
        "settings_fields": _DEFAULT_SECTION_SETTINGS_FIELDS,
    },

    # ------------------------------------------------------------- Gallery
    "gallery": {
        "label": "Galleria",
        "icon": "🖼️",
        "description": "Galleria di immagini con titoli opzionali.",
        "default_label": "Galleria",
        "section_template": "dynamic_gallery:grid",
        "fields": [
            _field("image", "Immagine", "image", required=True),
            _field("title", "Titolo", "text"),
            _field("description", "Descrizione", "textarea", rows=2),
        ],
        "item_label": lambda d: d.get("title") or "Foto",
        "examples": [],
        "settings_fields": _DEFAULT_SECTION_SETTINGS_FIELDS,
    },

    # ------------------------------------------------------------- Team
    "team": {
        "label": "Team",
        "icon": "👥",
        "description": "Membri del team con foto, ruolo e bio.",
        "default_label": "Il nostro team",
        "section_template": "dynamic_team:cards",
        "fields": [
            _field("name", "Nome", "text", required=True),
            _field("role", "Ruolo", "text"),
            _field("bio", "Bio", "textarea", rows=3),
            _field("image", "Foto", "image"),
            _field("email", "Email", "email"),
            _field("linkedin", "LinkedIn", "url"),
        ],
        "item_label": lambda d: d.get("name") or "Membro",
        "examples": [
            {"name": "Marco Rossi", "role": "Fondatore", "bio": "Esperto del settore con oltre 10 anni di esperienza."},
        ],
        "settings_fields": _DEFAULT_SECTION_SETTINGS_FIELDS,
    },

    # ------------------------------------------------------------- Testimonials
    "testimonials": {
        "label": "Testimonianze",
        "icon": "⭐",
        "description": "Recensioni di clienti con valutazione e foto.",
        "default_label": "Cosa dicono di noi",
        "section_template": "dynamic_testimonials:cards",
        "fields": [
            _field("name", "Nome cliente", "text", required=True),
            _field("role", "Ruolo / Azienda", "text"),
            _field("text", "Testimonianza", "textarea", required=True, rows=3),
            _field("rating", "Valutazione (1-5)", "number"),
            _field("image", "Foto", "image"),
        ],
        "item_label": lambda d: d.get("name") or "Testimonianza",
        "examples": [
            {"name": "Anna Bianchi", "role": "Cliente", "text": "Esperienza eccezionale, consiglio vivamente!", "rating": 5},
        ],
        "settings_fields": _DEFAULT_SECTION_SETTINGS_FIELDS,
    },

    # ------------------------------------------------------------- Services
    "services": {
        "label": "Servizi",
        "icon": "🛠️",
        "description": "Servizi offerti, con descrizione, prezzo e foto.",
        "default_label": "I nostri servizi",
        "section_template": "dynamic_services:cards",
        "fields": [
            _field("name", "Nome servizio", "text", required=True),
            _field("description", "Descrizione", "textarea", rows=3),
            _field("price", "Prezzo (opzionale)", "price"),
            _field("image", "Immagine", "image"),
            _field("highlight", "In evidenza", "boolean"),
        ],
        "item_label": lambda d: d.get("name") or "Servizio",
        "examples": [
            {"name": "Consulenza", "description": "Analisi iniziale e preventivo personalizzato.", "highlight": False},
        ],
        "settings_fields": _DEFAULT_SECTION_SETTINGS_FIELDS,
    },

    # ------------------------------------------------------------- Pricing
    "pricing": {
        "label": "Listino prezzi",
        "icon": "💶",
        "description": "Pacchetti / piani con prezzo e lista feature.",
        "default_label": "Listino",
        "section_template": "dynamic_pricing:tiers",
        "fields": [
            _field("name", "Nome pacchetto", "text", required=True),
            _field("price", "Prezzo", "price"),
            _field("period", "Periodo", "text", placeholder="Es. /mese, /anno, una tantum"),
            _field("features", "Feature incluse", "list", help="Una feature per riga."),
            _field("highlight", "Pacchetto in evidenza", "boolean"),
            _field("cta_label", "Testo bottone", "text", placeholder="Es. Scopri di più"),
            _field("cta_href", "Link bottone", "url"),
        ],
        "item_label": lambda d: d.get("name") or "Pacchetto",
        "examples": [
            {"name": "Base", "price": "29", "period": "/mese", "features": ["Feature 1", "Feature 2"], "highlight": False},
            {"name": "Pro", "price": "59", "period": "/mese", "features": ["Tutto Base", "Feature avanzata"], "highlight": True},
        ],
        "settings_fields": _DEFAULT_SECTION_SETTINGS_FIELDS,
    },

    # ------------------------------------------------------------- Products
    "products": {
        "label": "Prodotti",
        "icon": "🛍️",
        "description": "Catalogo prodotti con foto e prezzo.",
        "default_label": "Prodotti",
        "section_template": "dynamic_products:cards",
        "fields": [
            _field("name", "Nome prodotto", "text", required=True),
            _field("description", "Descrizione", "textarea", rows=2),
            _field("category", "Categoria", "text"),
            _field("price", "Prezzo", "price"),
            _field("image", "Foto", "image"),
            _field("in_stock", "Disponibile", "boolean"),
        ],
        "item_label": lambda d: d.get("name") or "Prodotto",
        "examples": [],
        "settings_fields": _DEFAULT_SECTION_SETTINGS_FIELDS,
    },

    # ------------------------------------------------------------- Events
    "events": {
        "label": "Eventi",
        "icon": "📅",
        "description": "Eventi con data, ora, luogo e descrizione.",
        "default_label": "Prossimi eventi",
        "section_template": "dynamic_events:cards",
        "fields": [
            _field("title", "Titolo evento", "text", required=True),
            _field("date", "Data", "date"),
            _field("time", "Ora", "time"),
            _field("location", "Luogo", "text"),
            _field("description", "Descrizione", "textarea", rows=3),
            _field("image", "Immagine", "image"),
            _field("link", "Link prenotazione", "url"),
        ],
        "item_label": lambda d: d.get("title") or "Evento",
        "examples": [],
        "settings_fields": _DEFAULT_SECTION_SETTINGS_FIELDS,
    },

    # ------------------------------------------------------------- Contact info
    "contact_info": {
        "label": "Informazioni di contatto",
        "icon": "📞",
        "description": "Coppie etichetta-valore (telefono, email, indirizzo, social).",
        "default_label": "Contatti",
        "section_template": "dynamic_contact:list",
        "fields": [
            _field("label", "Etichetta", "text", required=True, placeholder="Es. Telefono"),
            _field("value", "Valore", "text", required=True, placeholder="Es. +39 02 1234 5678"),
            _field("link", "Link (opzionale)", "url", help="Es. tel:+39021234, mailto:..."),
            _field("icon", "Icona", "select", options=[
                _opt("phone", "Telefono"),
                _opt("email", "Email"),
                _opt("location", "Indirizzo"),
                _opt("clock", "Orari"),
                _opt("instagram", "Instagram"),
                _opt("facebook", "Facebook"),
                _opt("whatsapp", "WhatsApp"),
                _opt("globe", "Sito web"),
            ]),
        ],
        "item_label": lambda d: d.get("label") or "Contatto",
        "examples": [
            {"label": "Telefono", "value": "+39 000 000 0000", "icon": "phone"},
            {"label": "Email", "value": "info@example.com", "icon": "email"},
            {"label": "Indirizzo", "value": "Via Roma 1, Milano", "icon": "location"},
        ],
        "settings_fields": _DEFAULT_SECTION_SETTINGS_FIELDS,
    },

    # ------------------------------------------------------------- Generic
    "generic": {
        "label": "Sezione personalizzata",
        "icon": "📋",
        "description": "Sezione libera con campi titolo / descrizione / immagine.",
        "default_label": "Sezione",
        "section_template": "dynamic_generic:table",
        "fields": [
            _field("title", "Titolo", "text"),
            _field("subtitle", "Sottotitolo", "text"),
            _field("description", "Descrizione", "textarea", rows=3),
            _field("image", "Immagine", "image"),
            _field("link", "Link", "url"),
        ],
        "item_label": lambda d: d.get("title") or "Voce",
        "examples": [],
        "settings_fields": _DEFAULT_SECTION_SETTINGS_FIELDS,
    },
}


# ---- Helpers -----------------------------------------------------------------

def get_kind(kind: str) -> dict[str, Any]:
    if kind not in KIND_REGISTRY:
        raise KeyError(f"Unknown CMS kind: {kind}")
    return KIND_REGISTRY[kind]


def available_kinds() -> list[dict[str, Any]]:
    """Return a serialisable summary of all registered kinds (for the admin UI)."""
    return [
        {
            "kind": k,
            "label": v["label"],
            "icon": v["icon"],
            "description": v["description"],
            "default_label": v["default_label"],
        }
        for k, v in KIND_REGISTRY.items()
    ]


def section_template_for(kind: str) -> tuple[str, str]:
    """Return (section_type, variant) for the given kind."""
    spec = get_kind(kind)
    raw = spec.get("section_template") or "dynamic_generic:table"
    if ":" in raw:
        stype, variant = raw.split(":", 1)
    else:
        stype, variant = raw, "default"
    return stype, variant


def coerce_field_value(field: dict, value: Any) -> Any:
    """Best-effort normalisation per field type. Never raises — returns the raw
    value if coercion fails."""
    ftype = field.get("type", "text")
    if value is None:
        return None
    if ftype == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ("1", "true", "on", "yes", "si", "sì")
        return bool(value)
    if ftype in ("number", "price"):
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            v = value.replace(",", ".").strip()
            try:
                if "." in v:
                    return float(v)
                return int(v)
            except ValueError:
                return value
        return value
    if ftype == "list":
        if isinstance(value, list):
            return [str(x).strip() for x in value if str(x).strip()]
        if isinstance(value, str):
            return [line.strip() for line in value.splitlines() if line.strip()]
        return []
    if ftype == "multiselect":
        if isinstance(value, list):
            return [str(x) for x in value]
        if isinstance(value, str):
            return [v.strip() for v in value.split(",") if v.strip()]
        return []
    return value


def validate_item_data(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    """Coerce + validate an item payload against the kind schema. Drops keys
    that are not part of the schema. Raises ValueError if a required field is
    missing."""
    spec = get_kind(kind)
    cleaned: dict[str, Any] = {}
    for field in spec["fields"]:
        key = field["key"]
        raw = data.get(key)
        cleaned[key] = coerce_field_value(field, raw)
        if field.get("required"):
            v = cleaned[key]
            if v is None or (isinstance(v, str) and not v.strip()) or v == [] or v == {}:
                raise ValueError(f"Campo obbligatorio mancante: {field['label']}")
    return cleaned


def validate_section_settings(kind: str, settings: dict[str, Any] | None) -> dict[str, Any]:
    """Coerce + validate the per-section settings (eyebrow / headline / ...)."""
    spec = get_kind(kind)
    settings = settings or {}
    cleaned: dict[str, Any] = {}
    for field in spec.get("settings_fields") or []:
        key = field["key"]
        cleaned[key] = coerce_field_value(field, settings.get(key))
    return cleaned


def iter_image_keys(kind: str) -> Iterable[str]:
    """Yield the field keys of type 'image' for a kind."""
    spec = get_kind(kind)
    for field in spec["fields"]:
        if field["type"] == "image":
            yield field["key"]

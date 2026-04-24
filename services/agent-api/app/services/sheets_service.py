"""Google Sheets integration — create, connect, fetch data via service account."""

import asyncio
import logging
import re
import time
import traceback
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Simple in-memory data cache: {sheet_id: (timestamp, data)}
_cache: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 60  # seconds


# ---- Default column definitions ----

_DEFAULT_COLUMNS: dict[str, list[str]] = {
    "menu": ["Nome", "Descrizione", "Categoria", "Prezzo", "Foto_URL"],
    "menu_items": ["Nome", "Descrizione", "Categoria", "Prezzo", "Foto_URL"],
    "hours": ["Giorno", "Apertura", "Chiusura", "Note"],
    "orari": ["Giorno", "Apertura", "Chiusura", "Note"],
    "opening_hours": ["Giorno", "Apertura", "Chiusura", "Note"],
    "team": ["Nome", "Ruolo", "Bio", "Foto_URL", "Email"],
    "staff": ["Nome", "Ruolo", "Bio", "Foto_URL"],
    "testimonials": ["Nome", "Testo", "Valutazione", "Azienda"],
    "recensioni": ["Nome", "Testo", "Valutazione", "Data"],
    "faq": ["Domanda", "Risposta", "Categoria"],
    "pricing": ["Piano", "Prezzo", "Periodo", "Features", "Evidenziato"],
    "prezzi": ["Servizio", "Prezzo", "Descrizione"],
    "events": ["Titolo", "Data", "Ora", "Descrizione", "Luogo"],
    "eventi": ["Titolo", "Data", "Ora", "Descrizione", "Luogo"],
    "gallery": ["Titolo", "Foto_URL", "Descrizione", "Categoria"],
    "galleria": ["Titolo", "Foto_URL", "Descrizione", "Categoria"],
    "products": ["Nome", "Descrizione", "Categoria", "Prezzo", "Foto_URL", "Disponibile"],
    "prodotti": ["Nome", "Descrizione", "Categoria", "Prezzo", "Foto_URL"],
    "services": ["Servizio", "Descrizione", "Prezzo", "Icona"],
    "servizi": ["Servizio", "Descrizione", "Prezzo", "Icona"],
    "contact": ["Campo", "Valore"],
    "contatti": ["Campo", "Valore"],
    "info": ["Campo", "Valore"],
    "news": ["Titolo", "Data", "Testo", "Autore", "Foto_URL"],
    "blog": ["Titolo", "Data", "Testo", "Autore", "Foto_URL"],
    "partners": ["Nome", "Logo_URL", "Sito_Web", "Descrizione"],
    "social": ["Piattaforma", "URL", "Label"],
}


def default_columns(section_name: str) -> list[str]:
    return _DEFAULT_COLUMNS.get(section_name.lower(), ["Titolo", "Descrizione", "Valore"])


def _normalise_section(s: dict | str) -> dict:
    """Accept either a full section dict or a bare name string."""
    if isinstance(s, str):
        name = s.strip().lower().replace(" ", "_")
        return {
            "name": name,
            "label": s.strip().title(),
            "columns": default_columns(name),
            "description": "",
        }
    name = s.get("name", "dati").lower().replace(" ", "_")
    return {
        "name": name,
        "label": s.get("label") or name.replace("_", " ").title(),
        "columns": s.get("columns") or default_columns(name),
        "description": s.get("description", ""),
    }


# ---- Helpers ----

def _extract_sheet_id(url: str) -> str:
    """Extract Google Sheet ID from a URL or return raw ID."""
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url)
    if match:
        return match.group(1)
    stripped = url.strip()
    if re.match(r"^[a-zA-Z0-9_-]{20,}$", stripped):
        return stripped
    raise ValueError(f"Cannot extract Sheet ID from: {url}")


def _get_credentials(credentials_path: str):
    from google.oauth2.service_account import Credentials
    if not Path(credentials_path).exists():
        raise FileNotFoundError(
            f"Google service account key not found at {credentials_path}. "
            "Place gsheets-credentials.json in the secrets/ folder on the VPS."
        )
    return Credentials.from_service_account_file(credentials_path, scopes=SCOPES)


def _sheets_svc(creds):
    from googleapiclient.discovery import build
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _drive_svc(creds):
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=creds, cache_discovery=False)


# ---- Synchronous implementations (run in thread pool) ----

def _sync_create_spreadsheet(
    credentials_path: str,
    title: str,
    sections: list,
    client_email: Optional[str],
    drive_folder_id: Optional[str] = None,
) -> dict:
    creds = _get_credentials(credentials_path)
    drive = _drive_svc(creds)
    sheets = _sheets_svc(creds)

    normalised = [_normalise_section(s) for s in sections] if sections else []

    # Determine target folder. The service account must have Editor access to this folder.
    # If no folder_id is provided the service account will try to create the file in its own
    # drive space — this typically returns 403 unless a shared drive is available.
    body = {
        "name": title,
        "mimeType": "application/vnd.google-apps.spreadsheet",
    }
    if drive_folder_id:
        body["parents"] = [drive_folder_id]

    file_meta = drive.files().create(
        body=body,
        fields="id,webViewLink",
        supportsAllDrives=True,
    ).execute()

    sheet_id = file_meta["id"]
    sheet_url = file_meta.get(
        "webViewLink", f"https://docs.google.com/spreadsheets/d/{sheet_id}"
    )

    # Add named tabs if sections are provided (the file starts with one default tab)
    if normalised:
        # Rename the first default tab to the first section name
        first_tab_title = normalised[0]["name"]
        meta = sheets.spreadsheets().get(
            spreadsheetId=sheet_id, fields="sheets.properties"
        ).execute()
        first_sheet_id = meta["sheets"][0]["properties"]["sheetId"]

        requests = [
            {
                "updateSheetProperties": {
                    "properties": {"sheetId": first_sheet_id, "title": first_tab_title},
                    "fields": "title",
                }
            }
        ]
        # Add remaining tabs
        for s in normalised[1:]:
            requests.append({"addSheet": {"properties": {"title": s["name"]}}})

        sheets.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id, body={"requests": requests}
        ).execute()

        # Write header rows in batch
        batch_data = [
            {"range": f"'{s['name']}'!A1", "values": [s["columns"]]}
            for s in normalised
        ]
        sheets.spreadsheets().values().batchUpdate(
            spreadsheetId=sheet_id,
            body={"valueInputOption": "RAW", "data": batch_data},
        ).execute()

    # Share with client email if provided
    if client_email:
        try:
            drive.permissions().create(
                fileId=sheet_id,
                body={"type": "user", "role": "writer", "emailAddress": client_email},
                sendNotificationEmail=True,
            ).execute()
        except Exception as e:
            logger.warning(f"Could not share spreadsheet with {client_email}: {e}")

    return {
        "sheet_id": sheet_id,
        "sheet_url": sheet_url,
        "sheet_title": title,
        "sections": normalised,
        "client_email": client_email,
    }


def _sync_connect_spreadsheet(credentials_path: str, sheet_url: str) -> dict:
    sheet_id = _extract_sheet_id(sheet_url)
    creds = _get_credentials(credentials_path)
    sheets = _sheets_svc(creds)

    meta = sheets.spreadsheets().get(
        spreadsheetId=sheet_id,
        fields="spreadsheetId,properties,sheets",
    ).execute()

    title = meta["properties"]["title"]
    tabs = []
    for s in meta.get("sheets", []):
        label = s["properties"]["title"]
        name = label.lower().replace(" ", "_")
        tabs.append({"name": name, "label": label, "columns": [], "description": ""})

    return {
        "sheet_id": sheet_id,
        "sheet_url": f"https://docs.google.com/spreadsheets/d/{sheet_id}",
        "sheet_title": title,
        "sections": tabs,
        "client_email": None,
    }


def _sync_fetch_data(credentials_path: str, sheet_id: str) -> dict:
    creds = _get_credentials(credentials_path)
    sheets = _sheets_svc(creds)

    meta = sheets.spreadsheets().get(
        spreadsheetId=sheet_id, fields="sheets"
    ).execute()
    sheet_names = [s["properties"]["title"] for s in meta.get("sheets", [])]

    result = {}
    for sheet_name in sheet_names:
        values = (
            sheets.spreadsheets()
            .values()
            .get(spreadsheetId=sheet_id, range=f"'{sheet_name}'")
            .execute()
            .get("values", [])
        )
        if not values or len(values) < 2:
            continue

        headers = values[0]
        rows = []
        for row in values[1:]:
            padded = row + [""] * (len(headers) - len(row))
            row_dict = {headers[i]: padded[i] for i in range(len(headers))}
            if any(str(v).strip() for v in row_dict.values()):
                rows.append(row_dict)

        if rows:
            key = sheet_name.lower().replace(" ", "_")
            result[key] = rows

    return result


def _sync_share(credentials_path: str, sheet_id: str, email: str) -> None:
    creds = _get_credentials(credentials_path)
    drive = _drive_svc(creds)
    drive.permissions().create(
        fileId=sheet_id,
        body={"type": "user", "role": "writer", "emailAddress": email},
        sendNotificationEmail=True,
    ).execute()


# ---- Public async API ----

async def create_spreadsheet(
    credentials_path: str,
    title: str,
    sections: list,
    client_email: Optional[str] = None,
    drive_folder_id: Optional[str] = None,
) -> dict:
    try:
        return await asyncio.to_thread(
            _sync_create_spreadsheet, credentials_path, title, sections, client_email, drive_folder_id
        )
    except Exception as e:
        logger.error(f"create_spreadsheet FAILED: {e}\n{traceback.format_exc()}")
        raise


async def connect_spreadsheet(credentials_path: str, sheet_url: str) -> dict:
    return await asyncio.to_thread(_sync_connect_spreadsheet, credentials_path, sheet_url)


async def fetch_data(credentials_path: str, sheet_id: str) -> dict:
    now = time.time()
    cached = _cache.get(sheet_id)
    if cached and (now - cached[0]) < _CACHE_TTL:
        return cached[1]
    data = await asyncio.to_thread(_sync_fetch_data, credentials_path, sheet_id)
    _cache[sheet_id] = (now, data)
    return data


async def share_with_email(credentials_path: str, sheet_id: str, email: str) -> None:
    await asyncio.to_thread(_sync_share, credentials_path, sheet_id, email)


def invalidate_cache(sheet_id: str) -> None:
    _cache.pop(sheet_id, None)

"""Telegram bot command handlers."""

import logging
from telegram import Update
from telegram.ext import ContextTypes

from bot import api_client
from bot.config import get_bot_settings
from bot import state_store

logger = logging.getLogger(__name__)
settings = get_bot_settings()

# State format (persisted in Redis):
#   {"action": "awaiting_brief"}
#   {"action": "collecting_assets", "brief": "...", "assets": [...]}


def is_authorized(chat_id: int) -> bool:
    """Check if the chat is authorized (admin only in v1)."""
    return str(chat_id) == settings.telegram_admin_chat_id


HELP_TEXT = (
    "🏗 *AI Site System*\n\n"
    "Comandi:\n"
    "/new — crea un nuovo sito (guidato)\n"
    "/done — termina l'upload asset e avvia la build\n"
    "/modify `<project_id>` `<descrizione modifica>` — modifica un sito esistente\n"
    "/status `<job_id>` — controlla lo stato di un job\n"
    "/projects — elenca tutti i progetti\n"
    "/revisions `<project_id>` — elenca le revisioni di un progetto\n"
    "/approve `<project_id>` `<revision_id>` — approva una revisione\n"
    "/reject `<project_id>` `<revision_id>` `[motivo]` — rifiuta una revisione\n"
    "/latest — ultimi job\n"
    "/help — mostra questo messaggio"
)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_chat.id):
        await update.message.reply_text("Non autorizzato. Contatta l'amministratore.")
        return
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start new website creation. Usage: /new <brief>"""
    if not is_authorized(update.effective_chat.id):
        await update.message.reply_text("Non autorizzato.")
        return

    text = update.message.text.replace("/new", "", 1).strip()
    chat_id = update.effective_chat.id

    if not text:
        await state_store.set_state(chat_id, {"action": "awaiting_brief"})
        await update.message.reply_text(
            "📝 *Step 1/2 — Brief*\n\n"
            "Descrivi il sito: obiettivo, pubblico, sezioni chiave, tono, requisiti specifici.",
            parse_mode="Markdown",
        )
        return

    await state_store.set_state(
        chat_id, {"action": "collecting_assets", "brief": text, "assets": []}
    )
    await update.message.reply_text(
        "📎 *Step 2/2 — Asset (opzionale)*\n\n"
        "Invia *logo* o *immagini di riferimento* per il design.\n"
        "• Aggiungi una didascalia per ciascuna immagine (es. \"logo aziendale\" o \"riferimento colori\")\n"
        "• Invia /done quando hai finito, oppure /done subito per saltare",
        parse_mode="Markdown",
    )


async def _create_website(update: Update, brief: str, uploaded_assets: list[dict] | None = None):
    await update.message.reply_text("⏳ Avvio creazione sito…")
    try:
        result = await api_client.create_website(brief, uploaded_assets=uploaded_assets)
        asset_note = f"\nAsset inclusi: {len(uploaded_assets)}" if uploaded_assets else ""
        await update.message.reply_text(
            f"✅ *Creazione sito avviata*\n\n"
            f"Job ID: `{result['job_id']}`\n"
            f"Status: {result['status']}{asset_note}\n\n"
            f"Ti avviserò quando la preview è pronta.",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.exception("Failed to create website")
        await update.message.reply_text(f"❌ Errore avvio creazione: {e}")


async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not is_authorized(chat_id):
        return

    state = await state_store.get_state(chat_id)
    if not state or state.get("action") != "collecting_assets":
        await update.message.reply_text("Niente in corso. Usa /new per iniziare.")
        return

    brief = state["brief"]
    assets = state.get("assets", [])
    await state_store.clear_state(chat_id)

    await _create_website(update, brief, uploaded_assets=assets if assets else None)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not is_authorized(chat_id):
        return

    state = await state_store.get_state(chat_id)
    if not state or state.get("action") != "collecting_assets":
        await update.message.reply_text(
            "Usa prima /new per iniziare una creazione sito, poi ti chiederò le immagini."
        )
        return

    caption = (update.message.caption or "").strip()

    asset_type = "logo" if any(
        kw in caption.lower() for kw in ("logo", "brand", "icon", "mark")
    ) else "reference"

    try:
        if update.message.photo:
            photo = update.message.photo[-1]
            tg_file = await context.bot.get_file(photo.file_id)
            content_type = "image/jpeg"
            filename = f"{photo.file_id}.jpg"
        else:
            doc = update.message.document
            tg_file = await context.bot.get_file(doc.file_id)
            content_type = doc.mime_type or "image/png"
            filename = doc.file_name or f"{doc.file_id}.png"

        file_bytes = await tg_file.download_as_bytearray()
        asset = await api_client.upload_asset(
            file_bytes=bytes(file_bytes),
            filename=filename,
            content_type=content_type,
            asset_type=asset_type,
            description=caption,
        )
        state.setdefault("assets", []).append(asset)
        await state_store.set_state(chat_id, state)
        count = len(state["assets"])

        type_label = "🖼 Logo" if asset_type == "logo" else "🎨 Reference"
        await update.message.reply_text(
            f"{type_label} ricevuto ({count} totali).\n"
            f"Continua a inviare immagini o invia /done per avviare la build.",
        )

    except Exception as e:
        logger.exception("Failed to upload asset from Telegram")
        await update.message.reply_text(f"❌ Impossibile caricare l'immagine: {e}")


async def cmd_modify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Modify existing website. Usage: /modify <project_id> <changes>"""
    if not is_authorized(update.effective_chat.id):
        await update.message.reply_text("Non autorizzato.")
        return

    text = update.message.text.replace("/modify", "", 1).strip()
    parts = text.split(maxsplit=1)

    if len(parts) < 2:
        try:
            result = await api_client.list_projects()
            projects = result.get("projects", [])
            if not projects:
                await update.message.reply_text("Nessun progetto. Usa /new per crearne uno.")
                return

            lines = ["📋 *Progetti:*\n"]
            for p in projects[:20]:
                lines.append(f"• `{p['id'][:8]}` — {p['name']} ({p['status']})")
            lines.append("\nUso: /modify `<project_id>` `<cosa cambiare>`")

            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        except Exception as e:
            await update.message.reply_text(f"❌ Errore lista progetti: {e}")
        return

    project_id = parts[0]
    change_request = parts[1]

    await update.message.reply_text("⏳ Avvio modifica sito…")
    try:
        result = await api_client.modify_website(project_id, change_request)
        await update.message.reply_text(
            f"✅ *Modifica avviata*\n\n"
            f"Job ID: `{result['job_id']}`\n"
            f"Change Request ID: `{result['change_request_id']}`\n"
            f"Status: {result['status']}\n\n"
            f"Ti avviserò quando la preview modificata è pronta.",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.exception("Failed to modify website")
        await update.message.reply_text(f"❌ Errore avvio modifica: {e}")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_chat.id):
        return

    text = update.message.text.replace("/status", "", 1).strip()
    if not text:
        await update.message.reply_text("Uso: /status `<job_id>`", parse_mode="Markdown")
        return

    try:
        result = await api_client.get_job_status(text)
        msg = (
            f"📊 *Job Status*\n\n"
            f"ID: `{result['id']}`\n"
            f"Type: {result['job_type']}\n"
            f"Status: *{result['status']}*\n"
        )
        if result.get("error_message"):
            msg += f"Errore: {result['error_message']}\n"
        if result.get("result", {}).get("preview_url"):
            msg += f"\n🔗 Preview: {result['result']['preview_url']}"

        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Errore: {e}")


async def cmd_projects(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_chat.id):
        return

    try:
        result = await api_client.list_projects()
        projects = result.get("projects", [])
        if not projects:
            await update.message.reply_text("Nessun progetto. Usa /new per iniziare.")
            return

        lines = [f"📋 *Progetti ({result.get('total', 0)}):*\n"]
        for p in projects[:20]:
            status_emoji = {"active": "🟢", "archived": "⚪"}.get(p["status"], "🔵")
            lines.append(f"{status_emoji} *{p['name']}*\n   `{p['id'][:8]}` — {p['slug']}")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Errore: {e}")


async def cmd_revisions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List revisions of a project. Usage: /revisions <project_id>"""
    if not is_authorized(update.effective_chat.id):
        return

    text = update.message.text.replace("/revisions", "", 1).strip()
    if not text:
        await update.message.reply_text("Uso: /revisions `<project_id>`", parse_mode="Markdown")
        return
    try:
        revisions = await api_client.list_revisions(text.split()[0])
        if not revisions:
            await update.message.reply_text("Nessuna revisione.")
            return
        lines = [f"📋 *Revisioni:*\n"]
        for r in revisions[:20]:
            lines.append(f"• `{r['id'][:8]}` — #{r.get('revision_number')} — {r.get('status')}")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Errore: {e}")


async def cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Approve a revision. Usage: /approve <project_id> <revision_id>"""
    if not is_authorized(update.effective_chat.id):
        return

    parts = update.message.text.replace("/approve", "", 1).strip().split()
    if len(parts) < 2:
        await update.message.reply_text(
            "Uso: /approve `<project_id>` `<revision_id>`", parse_mode="Markdown"
        )
        return

    try:
        await api_client.approve_revision(parts[0], parts[1], "approved")
        await update.message.reply_text("✅ Revisione approvata e promossa in produzione.")
    except Exception as e:
        await update.message.reply_text(f"❌ Errore: {e}")


async def cmd_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reject a revision. Usage: /reject <project_id> <revision_id> [reason]"""
    if not is_authorized(update.effective_chat.id):
        return

    parts = update.message.text.replace("/reject", "", 1).strip().split(maxsplit=2)
    if len(parts) < 2:
        await update.message.reply_text(
            "Uso: /reject `<project_id>` `<revision_id>` `[motivo]`", parse_mode="Markdown"
        )
        return

    notes = parts[2] if len(parts) > 2 else None
    try:
        await api_client.approve_revision(parts[0], parts[1], "rejected", notes)
        await update.message.reply_text("❌ Revisione rifiutata.")
    except Exception as e:
        await update.message.reply_text(f"❌ Errore: {e}")


async def cmd_latest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_chat.id):
        return

    try:
        result = await api_client.api_request("GET", "/jobs", params={"limit": "10"})
        if not result:
            await update.message.reply_text("Nessun job.")
            return

        lines = ["📋 *Ultimi job:*\n"]
        status_emoji = {
            "new": "🆕", "planning": "📐", "building": "🔨", "modifying": "✏️",
            "qa": "🔍", "preview_ready": "👁", "awaiting_approval": "⏳",
            "deployed": "🚀", "failed": "❌",
        }
        for j in result[:10]:
            emoji = status_emoji.get(j["status"], "🔵")
            lines.append(f"{emoji} `{j['id'][:8]}` — {j['job_type']} — *{j['status']}*")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Errore: {e}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle non-command messages (for multi-step flows)."""
    chat_id = update.effective_chat.id
    if not is_authorized(chat_id):
        return

    state = await state_store.get_state(chat_id)

    if state and state.get("action") == "awaiting_brief":
        brief = update.message.text.strip()
        await state_store.set_state(
            chat_id, {"action": "collecting_assets", "brief": brief, "assets": []}
        )
        await update.message.reply_text(
            "📎 *Step 2/2 — Asset (opzionale)*\n\n"
            "Invia *logo* o *immagini di riferimento*.\n"
            "• Aggiungi una didascalia per ciascuna immagine\n"
            "• Invia /done quando hai finito",
            parse_mode="Markdown",
        )
        return

    if state and state.get("action") == "collecting_assets":
        extra = update.message.text.strip()
        state["brief"] = state.get("brief", "") + f"\n\nNote aggiuntive: {extra}"
        await state_store.set_state(chat_id, state)
        await update.message.reply_text(
            "📝 Note aggiunte al brief. Continua a inviare immagini o invia /done."
        )
        return

    await update.message.reply_text("Usa /help per vedere i comandi disponibili.")

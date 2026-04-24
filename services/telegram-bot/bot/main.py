"""Telegram Bot — FastAPI webhook server + bot initialization."""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, Header, HTTPException
from pydantic import BaseModel
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
)

from bot.config import get_bot_settings
from bot.logging_config import configure_logging
from bot.handlers import (
    cmd_start, cmd_help, cmd_new, cmd_modify,
    cmd_status, cmd_projects, cmd_approve, cmd_reject,
    cmd_latest, cmd_done, cmd_revisions, handle_message, handle_photo,
)

configure_logging("telegram-bot")
logger = logging.getLogger(__name__)

settings = get_bot_settings()

tg_app = (
    Application.builder()
    .token(settings.telegram_bot_token)
    .build()
)

tg_app.add_handler(CommandHandler("start", cmd_start))
tg_app.add_handler(CommandHandler("help", cmd_help))
tg_app.add_handler(CommandHandler("new", cmd_new))
tg_app.add_handler(CommandHandler("done", cmd_done))
tg_app.add_handler(CommandHandler("modify", cmd_modify))
tg_app.add_handler(CommandHandler("status", cmd_status))
tg_app.add_handler(CommandHandler("projects", cmd_projects))
tg_app.add_handler(CommandHandler("revisions", cmd_revisions))
tg_app.add_handler(CommandHandler("approve", cmd_approve))
tg_app.add_handler(CommandHandler("reject", cmd_reject))
tg_app.add_handler(CommandHandler("latest", cmd_latest))
tg_app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_photo))
tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))


@asynccontextmanager
async def lifespan(app: FastAPI):
    await tg_app.initialize()
    await tg_app.start()
    logger.info("Telegram bot started")
    yield
    await tg_app.stop()
    await tg_app.shutdown()
    logger.info("Telegram bot stopped")


app = FastAPI(title="AI Site System — Telegram Bot", lifespan=lifespan)


@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    secret_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if secret_token != settings.telegram_webhook_secret:
        return Response(status_code=403)

    data = await request.json()
    update = Update.de_json(data, tg_app.bot)
    await tg_app.process_update(update)
    return Response(status_code=200)


class NotifyRequest(BaseModel):
    message: str
    chat_id: str | None = None
    parse_mode: str | None = "Markdown"


@app.post("/notify")
async def notify(
    body: NotifyRequest,
    x_api_secret: str = Header("", alias="X-API-Secret"),
):
    """Authenticated internal endpoint the agent-api calls to push status updates."""
    if not settings.agent_api_secret or x_api_secret != settings.agent_api_secret:
        raise HTTPException(status_code=401, detail="Invalid API secret")

    chat_id = body.chat_id or settings.telegram_admin_chat_id
    if not chat_id:
        raise HTTPException(status_code=400, detail="No chat_id configured")

    try:
        await tg_app.bot.send_message(
            chat_id=int(chat_id),
            text=body.message,
            parse_mode=body.parse_mode,
            disable_web_page_preview=False,
        )
    except TelegramError as e:
        logger.warning(f"Telegram send failed: {e}")
        raise HTTPException(status_code=502, detail=f"Telegram error: {e}")

    return {"ok": True}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "telegram-bot"}


@app.get("/")
async def root():
    return {"service": "telegram-bot", "status": "running"}

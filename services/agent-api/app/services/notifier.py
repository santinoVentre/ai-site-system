"""Proactive Telegram notifier — agent-api -> telegram-bot /notify endpoint.

Replaces n8n polling-based notifier. Called on key job transitions.
"""

import logging

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

_settings = get_settings()


async def notify_telegram(message: str, chat_id: str | None = None) -> None:
    """Send a Telegram message via the bot's internal /notify endpoint.

    Silently logs on failure — notifications should never break the pipeline.
    """
    if not _settings.telegram_notify_enabled:
        return
    if not _settings.telegram_bot_url or not _settings.agent_api_secret:
        return

    url = f"{_settings.telegram_bot_url.rstrip('/')}/notify"
    payload: dict = {"message": message}
    if chat_id:
        payload["chat_id"] = chat_id

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url,
                json=payload,
                headers={"X-API-Secret": _settings.agent_api_secret},
            )
            if resp.is_error:
                logger.warning(f"Telegram notify failed {resp.status_code}: {resp.text[:200]}")
    except Exception as exc:
        logger.warning(f"Telegram notify crashed: {exc}")


async def notify_preview_ready(project_name: str, project_id: str, revision_id: str, preview_url: str, score: int | None = None) -> None:
    score_line = f"\nQuality score: *{score}/100*" if score is not None else ""
    msg = (
        f"✅ *Preview pronta — {project_name}*\n\n"
        f"Project: `{project_id}`\n"
        f"Revision: `{revision_id}`{score_line}\n"
        f"🔗 [Apri preview]({preview_url})\n\n"
        f"Per approvare:\n`/approve {project_id} {revision_id}`\n"
        f"Per rifiutare:\n`/reject {project_id} {revision_id} motivo`"
    )
    await notify_telegram(msg)


async def notify_job_failed(project_name: str | None, job_id: str, error: str) -> None:
    label = f" — {project_name}" if project_name else ""
    truncated = (error[:500] + "…") if len(error) > 500 else error
    msg = (
        f"❌ *Job fallito{label}*\n\n"
        f"Job ID: `{job_id}`\n"
        f"Errore: {truncated}\n\n"
        f"Retry:\n`/status {job_id}`"
    )
    await notify_telegram(msg)


async def notify_deployed(project_name: str, project_id: str, revision_number: int, live_url: str | None = None) -> None:
    link = f"\n🔗 {live_url}" if live_url else ""
    msg = (
        f"🚀 *Deploy live — {project_name}*\n\n"
        f"Project: `{project_id}`\n"
        f"Revision #{revision_number}{link}"
    )
    await notify_telegram(msg)

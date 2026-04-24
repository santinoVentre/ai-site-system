"""Redis-backed key/value store for multi-step command state.

Single-process in-memory state is lossy on restart and unsafe under scale.
This stores per-chat_id state in Redis with a TTL.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

import redis.asyncio as redis_asyncio

from bot.config import get_bot_settings

logger = logging.getLogger(__name__)

_STATE_TTL_SEC = 60 * 60  # 1 hour
_KEY_PREFIX = "tg:state:"

_client: Optional[redis_asyncio.Redis] = None


def _get_client() -> redis_asyncio.Redis:
    global _client
    if _client is None:
        settings = get_bot_settings()
        _client = redis_asyncio.from_url(
            settings.redis_url, decode_responses=True
        )
    return _client


def _key(chat_id: int | str) -> str:
    return f"{_KEY_PREFIX}{chat_id}"


async def get_state(chat_id: int | str) -> dict[str, Any] | None:
    try:
        raw = await _get_client().get(_key(chat_id))
    except Exception as exc:
        logger.warning(f"Redis get_state failed for {chat_id}: {exc}")
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


async def set_state(chat_id: int | str, state: dict[str, Any]) -> None:
    try:
        await _get_client().set(
            _key(chat_id), json.dumps(state, default=str), ex=_STATE_TTL_SEC
        )
    except Exception as exc:
        logger.warning(f"Redis set_state failed for {chat_id}: {exc}")


async def clear_state(chat_id: int | str) -> None:
    try:
        await _get_client().delete(_key(chat_id))
    except Exception as exc:
        logger.warning(f"Redis clear_state failed for {chat_id}: {exc}")

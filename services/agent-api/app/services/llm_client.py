"""LLM client — unified interface for OpenAI and Anthropic with robust retry."""

import json
import logging
from typing import Optional

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
    RetryError,
)

from app.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()


class LLMInfrastructureError(Exception):
    """Raised when the LLM API has a configuration/infrastructure problem (not fixable by retrying or changing code)."""
    pass


class LLMParseError(Exception):
    """Raised when the LLM returned content but it couldn't be parsed as JSON."""
    pass


class LLMTransientError(Exception):
    """Raised when the LLM call fails in a way that should be retried (rate limit, 5xx, timeout)."""
    pass


def _parse_json_robust(content: str, source: str = "") -> dict:
    """Parse JSON from LLM response, handling markdown fences, extra text, and truncation."""
    if content is None:
        raise LLMInfrastructureError(
            f"[{source}] LLM returned None content — the model may have returned a tool call, "
            "been filtered, or the API key/model configuration is incorrect. "
            "This is an infrastructure issue that cannot be fixed by retrying."
        )

    original = content

    if "```" in content:
        import re
        fenced = re.search(r'```(?:json)?\s*(\{.*)', content, re.DOTALL)
        if fenced:
            content = fenced.group(1)
            end = content.rfind("```")
            if end != -1:
                content = content[:end]

    start = content.find('{')
    end = content.rfind('}')
    if start != -1 and end != -1 and end > start:
        content = content[start:end + 1]

    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        logger.warning(f"[{source}] JSON parse error: {e}. Attempting repair.")

    repaired = _repair_truncated_json(content)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass

    try:
        from json_repair import repair_json
        repaired2 = repair_json(content, return_objects=True)
        if isinstance(repaired2, dict):
            logger.info(f"[{source}] json_repair succeeded")
            return repaired2
    except Exception:
        pass

    logger.error(f"[{source}] All JSON repair attempts failed. Content preview: {original[:200]!r}")
    raise LLMParseError(f"[{source}] All JSON repair attempts failed. Content preview: {original[:200]!r}")


def _repair_truncated_json(content: str) -> str:
    """Attempt to close a truncated JSON string by balancing brackets and quotes."""
    content = content.rstrip()
    import re
    content = re.sub(r',\s*"[^"]*$', '', content)
    content = re.sub(r',\s*"[^"]*":\s*"[^"]*$', '', content)
    content = re.sub(r',\s*"[^"]*":\s*\[[^\]]*$', '', content)

    opens = content.count('{') - content.count('}')
    arr_opens = content.count('[') - content.count(']')
    content += ']' * max(0, arr_opens) + '}' * max(0, opens)
    return content


def _is_transient_openai_error(exc: Exception) -> bool:
    """Identify errors worth retrying (429, 5xx, connection/timeout)."""
    try:
        import openai
    except ImportError:
        return False
    if isinstance(exc, openai.RateLimitError):
        return True
    if isinstance(exc, openai.APIConnectionError):
        return True
    if isinstance(exc, openai.APITimeoutError):
        return True
    if isinstance(exc, openai.InternalServerError):
        return True
    status = getattr(exc, "status_code", None)
    if status and 500 <= status < 600:
        return True
    return False


def _is_transient_anthropic_error(exc: Exception) -> bool:
    try:
        import anthropic
    except ImportError:
        return False
    if isinstance(exc, anthropic.RateLimitError):
        return True
    if isinstance(exc, anthropic.APIConnectionError):
        return True
    if isinstance(exc, anthropic.APITimeoutError):
        return True
    status = getattr(exc, "status_code", None)
    if status and 500 <= status < 600:
        return True
    return False


async def call_llm(
    system_prompt: str,
    user_prompt: str,
    response_format: Optional[str] = "json",
    model: Optional[str] = None,
    provider: Optional[str] = None,
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> dict | str:
    """Call LLM and return parsed JSON or raw text. Retries transient errors up to 3 times."""
    provider = provider or settings.default_llm_provider
    model = model or settings.default_llm_model

    if provider == "openai":
        return await _call_openai_with_retry(
            system_prompt, user_prompt, model, response_format, temperature, max_tokens
        )
    elif provider == "anthropic":
        return await _call_anthropic_with_retry(
            system_prompt, user_prompt, model, response_format, temperature, max_tokens
        )
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")


@retry(
    retry=retry_if_exception_type(LLMTransientError),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=2, max=20),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
async def _call_openai_with_retry(
    system_prompt: str,
    user_prompt: str,
    model: str,
    response_format: Optional[str],
    temperature: float,
    max_tokens: int,
) -> dict | str:
    try:
        return await _call_openai(
            system_prompt, user_prompt, model, response_format, temperature, max_tokens
        )
    except Exception as e:
        if _is_transient_openai_error(e):
            raise LLMTransientError(f"Transient OpenAI error: {e}") from e
        raise


@retry(
    retry=retry_if_exception_type(LLMTransientError),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=2, max=20),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
async def _call_anthropic_with_retry(
    system_prompt: str,
    user_prompt: str,
    model: str,
    response_format: Optional[str],
    temperature: float,
    max_tokens: int,
) -> dict | str:
    try:
        return await _call_anthropic(
            system_prompt, user_prompt, model, response_format, temperature, max_tokens
        )
    except Exception as e:
        if _is_transient_anthropic_error(e):
            raise LLMTransientError(f"Transient Anthropic error: {e}") from e
        raise


async def _call_openai(
    system_prompt: str,
    user_prompt: str,
    model: str,
    response_format: Optional[str],
    temperature: float,
    max_tokens: int,
) -> dict | str:
    import openai

    client_kwargs = {"api_key": settings.openai_api_key}
    if settings.openai_base_url:
        client_kwargs["base_url"] = settings.openai_base_url
    client = openai.AsyncOpenAI(**client_kwargs)

    if response_format == "json" and "json" not in system_prompt.lower() and "json" not in user_prompt.lower():
        system_prompt += "\n\nIMPORTANT: Respond with valid JSON only. No markdown, no code fences."

    kwargs = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format == "json":
        kwargs["response_format"] = {"type": "json_object"}

    response = await client.chat.completions.create(**kwargs)
    choice = response.choices[0]
    content = choice.message.content
    finish_reason = choice.finish_reason

    if content is None:
        if finish_reason == "tool_calls":
            detail = "model returned a tool call instead of text content — ensure the model supports json_object response_format and that no tools are configured"
        elif finish_reason == "content_filter":
            detail = "response was blocked by content filter — rephrase the request"
        else:
            detail = f"content is None with finish_reason='{finish_reason}' — check model name, API key, and that the model supports json_object format"
        raise LLMInfrastructureError(f"[openai] LLM returned empty content: {detail}")

    if response_format == "json":
        if finish_reason == "length":
            logger.warning("OpenAI response truncated (finish_reason=length), attempting JSON repair")
            content = _repair_truncated_json(content)
        return _parse_json_robust(content, "openai")
    return content


async def _call_anthropic(
    system_prompt: str,
    user_prompt: str,
    model: str,
    response_format: Optional[str],
    temperature: float,
    max_tokens: int,
) -> dict | str:
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    if response_format == "json":
        system_prompt += "\n\nIMPORTANT: Respond with valid JSON only. No markdown, no code fences."

    response = await client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    content = response.content[0].text

    if response_format == "json":
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0]
        return _parse_json_robust(content, "anthropic")
    return content

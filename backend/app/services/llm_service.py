"""LM Studio Local Server client – uses raw httpx, no openai SDK.

Settings (base_url, model, temperature, max_tokens) are read dynamically
from app.config so they can be changed at runtime via /api/settings.
"""

from __future__ import annotations

import json
import logging
from urllib.parse import urlparse
from typing import AsyncGenerator, Optional

import httpx

import app.config as cfg

logger = logging.getLogger(__name__)

# До 10 минут на ответ, чтобы долгие запросы не обрывались по таймауту.
_TIMEOUT = httpx.Timeout(connect=10.0, read=600.0, write=10.0, pool=10.0)


class LMStudioError(Exception):
    """Raised when LM Studio is unreachable or returns an error."""


def _assert_local_base_url(base_url: str) -> None:
    if not cfg.LOCAL_ONLY or cfg.ALLOW_REMOTE_LLM_ENDPOINT:
        return
    host = (urlparse(base_url).hostname or "").strip().lower()
    if host in {"localhost", "127.0.0.1", "::1"}:
        return
    raise LMStudioError(
        f"LOCAL_ONLY режим: запрещен внешний LLM endpoint ({base_url}). "
        "Используйте локальный LM Studio на localhost."
    )


async def chat_completion(
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout_seconds: float | None = None,
) -> str:
    """Send a chat-completion request to LM Studio Local Server.

    Returns the assistant message content as a plain string.
    All parameters fall back to current config values.
    """
    resolved_base_url = (base_url or cfg.LMSTUDIO_BASE_URL).strip()
    _assert_local_base_url(resolved_base_url)
    url = f"{resolved_base_url}/chat/completions"
    payload = {
        "model": model or cfg.LMSTUDIO_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature if temperature is not None else cfg.LMSTUDIO_TEMPERATURE,
        "max_tokens": max_tokens if max_tokens is not None else cfg.LMSTUDIO_MAX_TOKENS,
    }

    client_timeout = _TIMEOUT
    if timeout_seconds is not None:
        try:
            read_timeout = max(5.0, float(timeout_seconds))
        except (TypeError, ValueError):
            read_timeout = 600.0
        client_timeout = httpx.Timeout(connect=10.0, read=read_timeout, write=10.0, pool=10.0)

    try:
        async with httpx.AsyncClient(timeout=client_timeout) as client:
            response = await client.post(url, json=payload)
    except httpx.ConnectError as exc:
        logger.error("Cannot connect to LM Studio at %s: %s", url, exc)
        raise LMStudioError(
            f"Не удалось подключиться к LM Studio Local Server ({resolved_base_url}). "
            "Запусти LM Studio и включи Local Server (порт 1234)."
        ) from exc
    except httpx.TimeoutException as exc:
        logger.error("LM Studio request timed out: %s", exc)
        raise LMStudioError(
            "Запрос к LM Studio истёк по таймауту. "
            "Возможно, модель слишком большая или сервер перегружен."
        ) from exc

    if response.status_code != 200:
        detail = response.text[:500]
        logger.error("LM Studio returned %s: %s", response.status_code, detail)
        raise LMStudioError(
            f"LM Studio вернул HTTP {response.status_code}: {detail}"
        )

    data = response.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        logger.error("Unexpected LM Studio response structure: %s", data)
        raise LMStudioError(
            "Неожиданный формат ответа LM Studio."
        ) from exc


async def list_models(*, base_url: Optional[str] = None) -> list[str]:
    """Return model ids from LM Studio `/models` endpoint."""
    resolved_base_url = (base_url or cfg.LMSTUDIO_BASE_URL).strip()
    _assert_local_base_url(resolved_base_url)
    url = f"{resolved_base_url}/models"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            response = await client.get(url)
    except httpx.ConnectError as exc:
        logger.error("Cannot connect to LM Studio models endpoint at %s: %s", url, exc)
        raise LMStudioError(
            f"Не удалось подключиться к LM Studio Local Server ({resolved_base_url}). "
            "Запусти LM Studio и включи Local Server (порт 1234)."
        ) from exc
    except httpx.TimeoutException as exc:
        logger.error("LM Studio models request timed out: %s", exc)
        raise LMStudioError(
            "Проверка списка моделей в LM Studio истекла по таймауту."
        ) from exc

    if response.status_code != 200:
        detail = response.text[:500]
        logger.error("LM Studio /models returned %s: %s", response.status_code, detail)
        raise LMStudioError(f"LM Studio вернул HTTP {response.status_code}: {detail}")

    try:
        data = response.json()
    except Exception as exc:
        logger.error("Invalid JSON from LM Studio /models: %s", exc)
        raise LMStudioError("Неожиданный формат ответа LM Studio при запросе списка моделей.") from exc
    return [str(m.get("id", "")).strip() for m in (data.get("data") or []) if str(m.get("id", "")).strip()]


async def chat_completion_stream(
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
) -> AsyncGenerator[str, None]:
    """Stream chat-completion chunks from LM Studio (SSE). Yields content deltas."""
    resolved_base_url = (base_url or cfg.LMSTUDIO_BASE_URL).strip()
    _assert_local_base_url(resolved_base_url)
    url = f"{resolved_base_url}/chat/completions"
    payload = {
        "model": model or cfg.LMSTUDIO_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": True,
        "temperature": temperature if temperature is not None else cfg.LMSTUDIO_TEMPERATURE,
        "max_tokens": max_tokens if max_tokens is not None else cfg.LMSTUDIO_MAX_TOKENS,
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            async with client.stream("POST", url, json=payload) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    detail = (body.decode("utf-8", errors="replace"))[:500]
                    logger.error("LM Studio stream returned %s: %s", response.status_code, detail)
                    raise LMStudioError(
                        f"LM Studio вернул HTTP {response.status_code}: {detail}"
                    )
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:].strip()
                    if data == "[DONE]":
                        break
                    if not data:
                        continue
                    try:
                        obj = json.loads(data)
                        if isinstance(obj, dict):
                            err = obj.get("error")
                            if err:
                                raise LMStudioError(str(err))
                            if obj.get("object") == "error" and obj.get("message"):
                                raise LMStudioError(str(obj.get("message")))
                        choice = (obj.get("choices") or [{}])[0]
                        delta = choice.get("delta") or {}
                        content = delta.get("content")
                        if isinstance(content, str) and content:
                            yield content
                    except json.JSONDecodeError:
                        pass
    except httpx.ConnectError as exc:
        logger.error("Cannot connect to LM Studio at %s: %s", url, exc)
        raise LMStudioError(
            f"Не удалось подключиться к LM Studio Local Server ({resolved_base_url}). "
            "Запусти LM Studio и включи Local Server (порт 1234)."
        ) from exc
    except httpx.TimeoutException as exc:
        logger.error("LM Studio stream timed out: %s", exc)
        raise LMStudioError(
            "Запрос к LM Studio истёк по таймауту. "
            "Возможно, модель слишком большая или сервер перегружен."
        ) from exc

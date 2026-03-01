"""LM Studio Local Server client – uses raw httpx, no openai SDK.

Settings (base_url, model, temperature, max_tokens) are read dynamically
from app.config so they can be changed at runtime via /api/settings.
Vision ingest uses LM Studio /api/v1/chat with image input (sync for ingest).
"""

from __future__ import annotations

import base64
import json
import logging
from urllib.parse import urlparse
from typing import AsyncGenerator, Optional

import httpx

import app.config as cfg

logger = logging.getLogger(__name__)

# Prompt for vision model to describe image for document context
VISION_DESCRIBE_PROMPT = (
    "Кратко опиши изображение для контекста документа: что изображено, ключевые надписи или данные. "
    "Ответ только текстом описания на русском, без вводных фраз."
)

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


def describe_image_with_vision(
    image_bytes: bytes,
    mime_type: str = "image/png",
    context: Optional[str] = None,
    *,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    timeout_seconds: Optional[float] = None,
) -> Optional[str]:
    """Describe image using a vision-capable LLM (LM Studio /api/v1/chat).

    Used during ingest to add text descriptions of images into the document text.
    Sync so it can be called from sync ingest pipeline. Returns None on failure (caller may skip).
    """
    settings = cfg.get_vision_ingest_settings()
    if not settings.get("enabled"):
        logger.info("Vision ingest: disabled in settings, skipping image")
        return None
    base = (base_url or settings.get("base_url", "http://localhost:1234")).strip().rstrip("/")
    if not base:
        return None
    url_native = f"{base}/api/v1/chat"
    url_openai = f"{base.rstrip('/')}/chat/completions" if base.rstrip("/").endswith("/v1") else f"{base}/v1/chat/completions"
    model_id = (model or settings.get("model") or cfg.LMSTUDIO_MODEL).strip()
    if not model_id:
        return None
    timeout = timeout_seconds if timeout_seconds is not None else max(5, int(settings.get("timeout_seconds", 60)))
    try:
        data_url = f"data:{mime_type};base64,{base64.standard_b64encode(image_bytes).decode('ascii')}"
    except Exception as e:
        logger.warning("Vision ingest: failed to encode image: %s", e)
        return None

    prompt = VISION_DESCRIBE_PROMPT
    if context and str(context).strip():
        prompt = f"Контекст документа: {context.strip()}\n\n{prompt}"

    payload = {
        "model": model_id,
        "input": [
            {"type": "text", "content": prompt},
            {"type": "image", "data_url": data_url},
        ],
        "temperature": 0,
        "max_output_tokens": 512,
    }

    payload_openai = {
        "model": model_id,
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]},
        ],
        "temperature": 0,
        "max_tokens": 512,
    }
    last_exc: Exception | None = None
    response = None
    used_url = None
    for attempt in range(2):
        for url, payload_to_send in ((url_native, payload), (url_openai, payload_openai)):
            try:
                logger.info("Vision ingest: sending image to %s (model %s)", url, model_id)
                with httpx.Client(
                    timeout=httpx.Timeout(connect=10.0, read=float(timeout), write=float(timeout), pool=10.0)
                ) as client:
                    resp = client.post(url, json=payload_to_send)
            except httpx.ConnectError as exc:
                last_exc = exc
                logger.warning("Vision ingest: cannot connect to %s: %s", url, exc)
                continue
            except httpx.TimeoutException as exc:
                last_exc = exc
                logger.warning("Vision ingest: request timed out to %s: %s", url, exc)
                continue
            if resp.status_code == 404:
                continue
            if resp.status_code in (502, 503, 504) and attempt == 0:
                last_exc = RuntimeError(f"HTTP {resp.status_code}")
                break
            response = resp
            used_url = url
            break
        if response is not None:
            break
    else:
        logger.warning("Vision ingest: no 200 response after trying native and OpenAI endpoints (check Base URL and model)")
        return None

    if response is None or used_url is None or response.status_code != 200:
        if response is not None:
            logger.warning("Vision ingest: %s returned %s: %s", used_url, response.status_code, (response.text or "")[:300])
        return None

    try:
        data = response.json()
    except json.JSONDecodeError as exc:
        logger.warning("Vision ingest: invalid JSON from %s: %s", used_url, exc)
        return None

    def _normalize_content(raw) -> str | None:
        """Extract single string from content: string, or list of parts (OpenAI/LM Studio)."""
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        if isinstance(raw, list):
            for part in raw:
                if isinstance(part, dict):
                    t = part.get("text") or part.get("content")
                    if isinstance(t, str) and t.strip():
                        return t.strip()
                elif isinstance(part, str) and part.strip():
                    return part.strip()
        return None

    for item in (data.get("output") or []):
        if not isinstance(item, dict):
            continue
        content = item.get("content") or item.get("text")
        out = _normalize_content(content) if content is not None else None
        if out:
            logger.info("Vision ingest: got description (%s chars)", len(out))
            return out
    choices = data.get("choices") or []
    if choices and isinstance(choices[0], dict):
        msg = (choices[0].get("message") or {}).get("content")
        out = _normalize_content(msg) if msg is not None else None
        if out:
            logger.info("Vision ingest: got description from OpenAI format (%s chars)", len(out))
            return out
    # Fallback: any string content in response (some servers wrap differently)
    for key in ("content", "message", "text", "output_text"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            logger.info("Vision ingest: got description from key %r (%s chars)", key, len(val.strip()))
            return val.strip()
    if isinstance(data.get("output"), list) and data["output"]:
        first = data["output"][0]
        if isinstance(first, dict):
            for key in ("content", "text"):
                val = first.get(key)
                out = _normalize_content(val) if val is not None else None
                if out:
                    logger.info("Vision ingest: got description from output[0].%s (%s chars)", key, len(out))
                    return out

    def _find_first_long_string(obj, skip_keys: set) -> str | None:
        if isinstance(obj, str):
            s = obj.strip()
            if len(s) > 20 and s not in skip_keys:
                return s
        elif isinstance(obj, dict):
            for k, v in obj.items():
                if k in skip_keys:
                    continue
                r = _find_first_long_string(v, skip_keys)
                if r:
                    return r
        elif isinstance(obj, list):
            for v in obj:
                r = _find_first_long_string(v, skip_keys)
                if r:
                    return r
        return None

    skip = {"model", "model_instance_id", "id", "object", "created", "usage", "stats", "response_id"}
    fallback = _find_first_long_string(data, skip)
    if fallback:
        logger.info("Vision ingest: got description from fallback search (%s chars)", len(fallback))
        return fallback[:2000]

    logger.warning(
        "Vision ingest: no message content in response from %s (keys: %s). Body snippet: %s",
        used_url or url_native,
        list(data.keys())[:12],
        (response.text or "")[:500],
    )
    return None

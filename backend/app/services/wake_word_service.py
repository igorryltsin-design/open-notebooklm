"""Local wake-word detection via Vosk (streaming PCM16 audio)."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_vosk_model = None
_VOSK_MODEL_PATH = str(os.getenv("VOSK_MODEL_PATH", "/opt/vosk-models/vosk-model-small-ru-0.22")).strip()
_WAKE_SAMPLE_RATE = 16000


def _normalize_text(text: str) -> str:
    return " ".join(str(text or "").strip().lower().replace("ё", "е").split())


def _get_vosk_model():
    global _vosk_model
    if _vosk_model is not None:
        return _vosk_model
    try:
        from vosk import Model, SetLogLevel
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("Wake-word недоступен: не установлен vosk.") from exc

    model_path = Path(_VOSK_MODEL_PATH)
    if not model_path.exists():
        raise RuntimeError(
            f"Wake-word модель Vosk не найдена: {_VOSK_MODEL_PATH}. "
            "Пересоберите backend-образ (модель должна быть упакована в Docker)."
        )
    try:
        SetLogLevel(-1)
    except Exception:
        pass
    logger.info("Loading Vosk wake-word model from %s", model_path)
    _vosk_model = Model(str(model_path))
    return _vosk_model


def create_session(wake_word: str):
    """Create a new streaming wake-word detector session."""
    try:
        from vosk import KaldiRecognizer
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("Wake-word недоступен: vosk импорт не удался.") from exc
    wake_norm = _normalize_text(wake_word) or "гена"
    recognizer = KaldiRecognizer(_get_vosk_model(), _WAKE_SAMPLE_RATE)
    try:
        recognizer.SetWords(False)
        recognizer.SetPartialWords(False)
    except Exception:
        pass
    return {
        "recognizer": recognizer,
        "wake_word": wake_norm,
        "last_detect_ts": 0.0,
        "cooldown_sec": 1.8,
    }


def process_audio_chunk(session: dict, pcm16_bytes: bytes) -> dict | None:
    """Feed PCM16 mono/16k bytes. Returns event dict when useful."""
    if not pcm16_bytes:
        return None
    rec = session["recognizer"]
    wake_word = str(session.get("wake_word") or "гена")
    now = time.monotonic()
    detected = False
    text = ""
    event_type = "partial"

    try:
        if rec.AcceptWaveform(pcm16_bytes):
            obj = json.loads(rec.Result() or "{}")
            text = _normalize_text(obj.get("text", ""))
            event_type = "final"
        else:
            obj = json.loads(rec.PartialResult() or "{}")
            text = _normalize_text(obj.get("partial", ""))
            event_type = "partial"
    except Exception:
        return None

    if text and wake_word in text and (now - float(session.get("last_detect_ts") or 0.0)) >= float(session.get("cooldown_sec") or 1.8):
        session["last_detect_ts"] = now
        detected = True

    if detected:
        return {"type": "wake_detected", "text": text, "wake_word": wake_word}
    if event_type == "partial" and text:
        return {"type": "partial", "text": text}
    return None


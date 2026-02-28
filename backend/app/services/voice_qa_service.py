"""Voice Q&A orchestration: STT -> RAG/LLM -> optional TTS."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
import tempfile
import uuid
import wave
from pathlib import Path
from typing import Callable

from app.services import podcast_service
from app.services.llm_service import LMStudioError
from app.tts.dispatcher import synthesise_script as tts_synthesise_script
from app.tts.text_normalize import latin_to_russian_readable_keep_pauses

logger = logging.getLogger(__name__)

MAX_AUDIO_BYTES = 12 * 1024 * 1024
MAX_AUDIO_SECONDS = 75.0
MAX_QUESTION_CHARS = 1200
MAX_TTS_ANSWER_CHARS = 2200
STREAM_TTS_MIN_SEGMENT_CHARS = 48
STREAM_TTS_MAX_SEGMENT_CHARS = 240

STT_TIMEOUT_SECONDS = 90
QA_TIMEOUT_SECONDS = 150
TTS_TIMEOUT_SECONDS = 120

_ALLOWED_STT_MODELS = {"tiny", "base", "small"}
_WHISPER_MODEL_NAME = "small"
_WHISPER_MODEL_NAME = str(os.getenv("FASTER_WHISPER_MODEL", _WHISPER_MODEL_NAME)).strip().lower() or "small"
if _WHISPER_MODEL_NAME not in _ALLOWED_STT_MODELS:
    _WHISPER_MODEL_NAME = "small"
_WHISPER_DOWNLOAD_ROOT = str(os.getenv("FASTER_WHISPER_DOWNLOAD_ROOT", "/opt/faster-whisper-models")).strip()
_WHISPER_LOCAL_ONLY = str(os.getenv("HF_HUB_OFFLINE", "0")).strip().lower() in {"1", "true", "yes", "on"}
_whisper_models: dict[str, object] = {}
_FORCED_STT_LANGUAGE = "ru"
_STREAM_TTS_SOURCES_RE = re.compile(r"\n\s*Источники\s*:\s*", flags=re.IGNORECASE)
_STREAM_TTS_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+|(?<=[:;])\s+(?=[A-ZА-ЯЁ])|\n{2,}")


class VoiceQaStageError(Exception):
    def __init__(
        self,
        stage: str,
        message: str,
        *,
        status_code: int = 400,
        retryable: bool = False,
        code: str | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = str(stage or "unknown")
        self.message = str(message or "Ошибка voice Q&A")
        self.status_code = int(status_code)
        self.retryable = bool(retryable)
        self.code = str(code or "").strip() or None
        self.hint = str(hint or "").strip() or None

    def to_detail(self) -> dict:
        return {
            "type": "voice_qa_error",
            "stage": self.stage,
            "message": self.message,
            "code": self.code,
            "retryable": self.retryable,
            "hint": self.hint,
        }


def normalize_stt_model_name(stt_model: str | None) -> str:
    name = str(stt_model or "").strip().lower()
    if name in _ALLOWED_STT_MODELS:
        return name
    return _WHISPER_MODEL_NAME


def _resolve_whisper_model_ref(model_name: str) -> str:
    if not _WHISPER_DOWNLOAD_ROOT:
        return model_name
    try:
        root = Path(_WHISPER_DOWNLOAD_ROOT)
        # New Docker images preload multiple models into subfolders: /opt/faster-whisper-models/<name>
        subdir_bin = root / model_name / "model.bin"
        if subdir_bin.exists():
            return str(subdir_bin.parent)
        # Backward-compat for older images that preload a single model directly into the root.
        root_bin = root / "model.bin"
        if model_name == _WHISPER_MODEL_NAME and root_bin.exists():
            return str(root)
    except Exception:
        pass
    return model_name


def _get_whisper_model(stt_model: str | None = None):
    model_name = normalize_stt_model_name(stt_model)
    cached = _whisper_models.get(model_name)
    if cached is not None:
        return cached
    try:
        from faster_whisper import WhisperModel
    except Exception as exc:  # pragma: no cover - depends on optional runtime package
        raise RuntimeError(
            "STT недоступен: не установлен faster-whisper. "
            "Добавьте зависимость и пересоберите backend."
        ) from exc
    model_ref = _resolve_whisper_model_ref(model_name)
    logger.info(
        "Loading faster-whisper model: requested=%s resolved=%s (download_root=%s local_only=%s)",
        model_name,
        model_ref,
        _WHISPER_DOWNLOAD_ROOT or "default",
        _WHISPER_LOCAL_ONLY,
    )
    try:
        model = WhisperModel(
            model_ref,
            device="cpu",
            compute_type="int8",
            download_root=(_WHISPER_DOWNLOAD_ROOT or None),
            local_files_only=_WHISPER_LOCAL_ONLY,
        )
    except Exception as exc:
        msg = str(exc)
        if "cached snapshot" in msg.lower() or "hf_hub_offline" in msg.lower():
            raise RuntimeError(
                "STT-модель faster-whisper не найдена в локальном кеше контейнера при офлайн-режиме HF. "
                "Пересоберите backend-образ (в новой версии Dockerfile модель предзагружается)."
            ) from exc
        raise
    _whisper_models[model_name] = model
    return model


def _convert_to_wav_and_measure(audio_bytes: bytes, filename: str) -> tuple[Path, Path, float]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg не найден, STT не может обработать аудио")
    suffix = Path(filename or "question.webm").suffix or ".webm"
    workdir = Path(tempfile.mkdtemp(prefix="voiceqa_"))
    src = workdir / f"input{suffix}"
    wav_path = workdir / "input.wav"
    src.write_bytes(audio_bytes)

    proc = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(src),
            "-ar",
            "16000",
            "-ac",
            "1",
            "-acodec",
            "pcm_s16le",
            str(wav_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0 or not wav_path.exists():
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"Не удалось декодировать аудио вопроса: {detail or 'unknown error'}")

    with wave.open(str(wav_path), "rb") as wav:
        frames = wav.getnframes()
        rate = wav.getframerate() or 16000
        duration = frames / float(rate)

    return workdir, wav_path, float(duration)


def _transcribe_sync(
    audio_bytes: bytes,
    filename: str,
    on_partial: Callable[[str], None] | None = None,
    stt_model: str | None = None,
) -> tuple[str, float]:
    workdir: Path | None = None
    try:
        workdir, wav_path, duration = _convert_to_wav_and_measure(audio_bytes, filename)
        if duration > MAX_AUDIO_SECONDS:
            raise ValueError(
                f"Слишком длинный вопрос ({duration:.1f} c). "
                f"Максимум: {MAX_AUDIO_SECONDS:.0f} c."
            )

        model_name = normalize_stt_model_name(stt_model)
        model = _get_whisper_model(model_name)
        segments, info = model.transcribe(
            str(wav_path),
            beam_size=1,
            best_of=1,
            vad_filter=True,
            condition_on_previous_text=False,
            language=_FORCED_STT_LANGUAGE,
        )
        parts: list[str] = []
        for seg in segments:
            seg_text = re.sub(r"\s+", " ", str(getattr(seg, "text", "") or "")).strip()
            if not seg_text:
                continue
            parts.append(seg_text)
            if on_partial:
                partial_text = " ".join(parts).strip()
                if partial_text:
                    try:
                        on_partial(partial_text)
                    except Exception:
                        # UI updates are best-effort; don't fail STT on callback issues.
                        pass
        text = " ".join(parts).strip()
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            raise ValueError("Не удалось распознать вопрос. Попробуйте говорить чуть громче или ближе к микрофону.")
        lang = getattr(info, "language", None)
        prob = getattr(info, "language_probability", None)
        logger.info(
            "voice_qa.stt.done model=%s duration=%.2fs forced_lang=%s lang=%s lang_prob=%s text_len=%d",
            model_name,
            duration,
            _FORCED_STT_LANGUAGE,
            lang or "unknown",
            f"{prob:.3f}" if isinstance(prob, (int, float)) else "n/a",
            len(text),
        )
        return text, duration
    finally:
        if workdir is not None:
            shutil.rmtree(workdir, ignore_errors=True)


async def transcribe_audio(
    audio_bytes: bytes,
    filename: str,
    *,
    stt_model: str | None = None,
) -> tuple[str, float]:
    return await transcribe_audio_streaming(audio_bytes, filename, on_partial=None, stt_model=stt_model)


async def transcribe_audio_streaming(
    audio_bytes: bytes,
    filename: str,
    on_partial: Callable[[str], None] | None = None,
    stt_model: str | None = None,
) -> tuple[str, float]:
    if len(audio_bytes) > MAX_AUDIO_BYTES:
        raise ValueError(
            f"Файл вопроса слишком большой ({len(audio_bytes) / (1024 * 1024):.1f} MB). "
            f"Максимум: {MAX_AUDIO_BYTES / (1024 * 1024):.0f} MB."
        )
    model_name = normalize_stt_model_name(stt_model)
    logger.info(
        "voice_qa.stt.start model=%s forced_lang=%s bytes=%d filename=%s",
        model_name,
        _FORCED_STT_LANGUAGE,
        len(audio_bytes),
        filename or "unknown",
    )
    return await asyncio.wait_for(
        asyncio.to_thread(_transcribe_sync, audio_bytes, filename, on_partial, model_name),
        timeout=STT_TIMEOUT_SECONDS,
    )


def _prepare_text_for_tts(answer_text: str, *, clamp_len: bool) -> str:
    text = str(answer_text or "").strip()
    if not text:
        return ""
    parts = re.split(r"\n\s*Источники\s*:\s*", text, maxsplit=1, flags=re.IGNORECASE)
    text = parts[0].strip()
    text = re.sub(r"\[[^\]]+\]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if clamp_len and len(text) > MAX_TTS_ANSWER_CHARS:
        text = text[:MAX_TTS_ANSWER_CHARS].rstrip() + "..."
    return latin_to_russian_readable_keep_pauses(text)


def _prepare_answer_for_tts(answer_text: str) -> str:
    return _prepare_text_for_tts(answer_text, clamp_len=True)


def _split_long_stream_tts_segment(text: str) -> list[str]:
    norm = re.sub(r"\s+", " ", str(text or "")).strip()
    if not norm:
        return []
    if len(norm) <= STREAM_TTS_MAX_SEGMENT_CHARS:
        return [norm]
    out: list[str] = []
    rest = norm
    while len(rest) > STREAM_TTS_MAX_SEGMENT_CHARS:
        max_len = STREAM_TTS_MAX_SEGMENT_CHARS
        scan = rest[: max_len + 40]
        cut = scan.rfind(", ", max_len // 2, len(scan))
        if cut < 0:
            cut = scan.rfind(" ", max_len // 2, len(scan))
        if cut < 0:
            cut = max_len
        piece = rest[:cut].strip(" ,")
        if piece:
            out.append(piece)
        rest = rest[cut:].lstrip(" ,")
        if not rest:
            break
    if rest:
        out.append(rest)
    return out


def split_stream_tts_segments(buffer_text: str, *, final: bool = False) -> tuple[list[str], str, bool]:
    """Extract phrase-sized TTS segments from an incremental LLM answer buffer.

    Returns (ready_segments, remaining_buffer, sources_section_started).
    Once "Источники:" appears, the function stops emitting/keeping the tail for TTS.
    """
    src = str(buffer_text or "")
    if not src:
        return [], "", False
    source_match = _STREAM_TTS_SOURCES_RE.search(src)
    source_cut = source_match is not None
    work = src[: source_match.start()] if source_match else src
    work = work.replace("\r\n", "\n").strip()
    if not work:
        return [], "", source_cut

    parts = [p.strip() for p in _STREAM_TTS_SPLIT_RE.split(work) if p and p.strip()]
    if not parts:
        return [], ("" if (final or source_cut) else work), source_cut

    ready: list[str] = []
    carry = ""
    consume_upto = len(parts) if (final or source_cut) else max(0, len(parts) - 1)
    for idx in range(consume_upto):
        part = parts[idx]
        merged = f"{carry} {part}".strip() if carry else part
        if len(merged) < STREAM_TTS_MIN_SEGMENT_CHARS:
            carry = merged
            continue
        ready.extend(_split_long_stream_tts_segment(merged))
        carry = ""

    remainder_parts: list[str] = []
    if carry:
        remainder_parts.append(carry)
    if not (final or source_cut) and parts:
        remainder_parts.append(parts[-1])
    remainder = " ".join([p for p in remainder_parts if p]).strip()

    if final or source_cut:
        if remainder:
            ready.extend(_split_long_stream_tts_segment(remainder))
        remainder = ""
    return ready, remainder, source_cut


async def _synth_answer_tts(document_id: str, answer_text: str) -> str | None:
    tts_text = _prepare_answer_for_tts(answer_text)
    if not tts_text:
        return None
    render_id = f"{document_id}_voiceqa_{uuid.uuid4().hex[:8]}"
    logger.info("voice_qa.tts.start document_id=%s chars=%d", document_id, len(tts_text))
    mp3_path = await asyncio.wait_for(
        tts_synthesise_script(
            [{"voice": "host", "text": tts_text}],
            render_id,
            progress_cb=None,
            apply_music=False,
            apply_postprocess=False,
        ),
        timeout=TTS_TIMEOUT_SECONDS,
    )
    logger.info("voice_qa.tts.done file=%s", mp3_path.name)
    return mp3_path.name


async def synth_answer_tts(document_id: str, answer_text: str) -> str | None:
    """Public wrapper for answer TTS synthesis used by streaming endpoint."""
    return await _synth_answer_tts(document_id, answer_text)


async def synth_answer_tts_segment(document_id: str, segment_text: str, segment_index: int) -> str | None:
    """Synthesize one incremental TTS segment (used by streaming voice endpoint)."""
    tts_text = _prepare_text_for_tts(segment_text, clamp_len=False)
    if not tts_text:
        return None
    render_id = f"{document_id}_voiceqa_seg{int(segment_index):03d}_{uuid.uuid4().hex[:8]}"
    logger.info(
        "voice_qa.tts.segment.start document_id=%s idx=%d chars=%d",
        document_id,
        int(segment_index),
        len(tts_text),
    )
    mp3_path = await asyncio.wait_for(
        tts_synthesise_script(
            [{"voice": "host", "text": tts_text}],
            render_id,
            progress_cb=None,
            apply_music=False,
            apply_postprocess=False,
        ),
        timeout=TTS_TIMEOUT_SECONDS,
    )
    logger.info("voice_qa.tts.segment.done idx=%d file=%s", int(segment_index), mp3_path.name)
    return mp3_path.name


async def run_voice_qa(
    *,
    document_id: str,
    document_ids: list[str] | None = None,
    audio_bytes: bytes,
    filename: str,
    strict_sources: bool = False,
    use_summary_context: bool = False,
    question_mode: str | None = None,
    answer_length: str | None = None,
    knowledge_mode: str | None = None,
    chat_mode: str = "qa",
    history: list[dict] | None = None,
    with_tts: bool = True,
    stt_model: str | None = None,
) -> dict:
    qa_document_ids = [str(x).strip() for x in (document_ids or [document_id]) if str(x).strip()]
    if not qa_document_ids:
        qa_document_ids = [str(document_id).strip()]
    stt_model_name = normalize_stt_model_name(stt_model)
    try:
        question_text, duration = await transcribe_audio(audio_bytes, filename, stt_model=stt_model_name)
    except asyncio.TimeoutError as e:
        raise VoiceQaStageError(
            "stt",
            f"STT не успел распознать вопрос (таймаут {STT_TIMEOUT_SECONDS} с).",
            status_code=504,
            retryable=True,
            code="stt_timeout",
            hint="Попробуйте более короткий вопрос или уменьшите качество/длину записи.",
        ) from e
    except ValueError as e:
        raise VoiceQaStageError(
            "stt",
            str(e),
            status_code=400,
            retryable=False,
            code="stt_invalid_audio",
            hint="Проверьте микрофон и попробуйте перезаписать вопрос.",
        ) from e
    except RuntimeError as e:
        hint = "Проверьте наличие ffmpeg и faster-whisper на backend."
        code = "stt_unavailable"
        if "локальном кеше контейнера" in str(e).lower():
            hint = "Пересоберите backend-контейнер: whisper-модель должна быть предзагружена в образ."
            code = "stt_model_cache_miss"
        raise VoiceQaStageError(
            "stt",
            str(e),
            status_code=503,
            retryable=False,
            code=code,
            hint=hint,
        ) from e

    if len(question_text) > MAX_QUESTION_CHARS:
        raise VoiceQaStageError(
            "stt",
            f"Распознанный вопрос слишком длинный ({len(question_text)} символов). Максимум: {MAX_QUESTION_CHARS}.",
            status_code=400,
            retryable=False,
            code="question_too_long",
            hint="Задайте вопрос короче или разделите его на несколько.",
        )

    logger.info(
        "voice_qa.llm.start document_id=%s docs=%d chat_mode=%s strict=%s mode=%s q_len=%d",
        document_id,
        len(qa_document_ids),
        chat_mode,
        strict_sources,
        question_mode or "default",
        len(question_text),
    )
    normalized_chat_mode = str(chat_mode or "qa").strip().lower()
    if normalized_chat_mode == "conv":
        qa_coro = podcast_service.answer_question_conversational(
            qa_document_ids,
            question_text,
            history or [],
            strict_sources=strict_sources,
            use_summary_context=use_summary_context,
            question_mode=question_mode,
            answer_length=answer_length,
            knowledge_mode=knowledge_mode,
        )
    else:
        qa_coro = podcast_service.answer_question(
            qa_document_ids,
            question_text,
            strict_sources=strict_sources,
            use_summary_context=use_summary_context,
            question_mode=question_mode,
            answer_length=answer_length,
            knowledge_mode=knowledge_mode,
        )
    try:
        result = await asyncio.wait_for(qa_coro, timeout=QA_TIMEOUT_SECONDS)
    except asyncio.TimeoutError as e:
        raise VoiceQaStageError(
            "llm",
            f"Генерация ответа превысила таймаут ({QA_TIMEOUT_SECONDS} с).",
            status_code=504,
            retryable=True,
            code="llm_timeout",
            hint="Попробуйте режим ответа 'Короткий' или уточните вопрос.",
        ) from e
    except LMStudioError as e:
        raise VoiceQaStageError(
            "llm",
            str(e),
            status_code=503,
            retryable=True,
            code="llm_unavailable",
            hint="Проверьте LM Studio Local Server и загруженную модель.",
        ) from e
    except ValueError as e:
        # Usually retrieval/index/strict-sources validation problems.
        raise VoiceQaStageError(
            "rag",
            str(e),
            status_code=400,
            retryable=False,
            code="rag_validation",
            hint="Проверьте индексацию документа или отключите строгий режим.",
        ) from e
    answer_text = str(result.get("answer", "") or "").strip()
    sources = result.get("citations") or []
    confidence = result.get("confidence")
    confidence_breakdown = result.get("confidence_breakdown") if isinstance(result.get("confidence_breakdown"), dict) else None
    mode = result.get("mode")
    logger.info(
        "voice_qa.llm.done document_id=%s docs=%d answer_len=%d sources=%d",
        document_id,
        len(qa_document_ids),
        len(answer_text),
        len(sources),
    )

    audio_filename = None
    if with_tts and answer_text:
        try:
            audio_filename = await _synth_answer_tts(document_id, answer_text)
        except asyncio.TimeoutError as e:
            raise VoiceQaStageError(
                "tts",
                f"Озвучка ответа не успела завершиться (таймаут {TTS_TIMEOUT_SECONDS} с).",
                status_code=504,
                retryable=True,
                code="tts_timeout",
                hint="Попробуйте более короткий ответ или повторите запрос позже.",
            ) from e
        except RuntimeError as e:
            raise VoiceQaStageError(
                "tts",
                str(e),
                status_code=503,
                retryable=True,
                code="tts_unavailable",
                hint="Проверьте локальные TTS модели/бинарники и настройки голосов.",
            ) from e

    return {
        "document_id": document_id,
        "document_ids": qa_document_ids,
        "question_text": question_text,
        "answer_text": answer_text,
        "sources": sources,
        "confidence": confidence,
        "confidence_breakdown": confidence_breakdown,
        "mode": mode,
        "chat_mode": normalized_chat_mode,
        "audio_filename": audio_filename,
        "audio_duration_sec": round(duration, 2),
        "stt_model": stt_model_name,
    }

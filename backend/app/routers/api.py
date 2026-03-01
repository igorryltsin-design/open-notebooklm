"""FastAPI routes — all /api/* endpoints."""

from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import re
import shutil
import uuid
import zipfile
from datetime import datetime
from queue import Empty, Queue
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, UploadFile, File, BackgroundTasks, Form, Request, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import FileResponse, StreamingResponse

from app.config import (
    DATA_DIR,
    INPUTS_DIR,
    INDEX_DIR,
    OUTPUTS_DIR,
    get_lmstudio_settings,
    update_lmstudio_settings,
    get_voice_settings,
    update_voice_settings,
    get_pronunciation_overrides,
    update_pronunciation_overrides,
    get_music_settings,
    update_music_settings,
    get_postprocess_settings,
    update_postprocess_settings,
    get_ocr_settings,
    update_ocr_settings,
    get_vision_ingest_settings,
    update_vision_ingest_settings,
    get_role_llm_overrides,
    update_role_llm_overrides,
    get_style_profiles,
    upsert_style_profile,
    delete_style_profile,
)
from app.models import (
    AudioJobRequest,
    DialogueLine,
    IngestResponse,
    JobInfo,
    JobStatus,
    LMStudioSettings,
    MusicSettings,
    OcrSettings,
    PostprocessSettings,
    VisionIngestSettings,
    PodcastScriptRequest,
    PodcastScriptResponse,
    RoleLlmSettingsResponse,
    SummaryResponse,
    UploadResponse,
    VoiceQaResponse,
    VoiceSettingsResponse,
)
from app import document_store, job_manager, project_store
from app import chat_store
from app.services import ingest_service, podcast_service, rag_service
from app.services import llm_service
from app.services import voice_qa_service
from app.services import wake_word_service
from app.services.voice_qa_service import VoiceQaStageError
from app.services import script_export_service
from app.services import script_metrics_service
from app.services.tts_quality_service import analyse_script
from app.services.llm_service import LMStudioError, chat_completion_stream
from app.tts.dispatcher import synthesise_script as tts_synthesise_script
from app.tts.text_normalize import latin_to_russian_readable_keep_pauses

router = APIRouter(prefix="/api")

# In-memory store for parsed texts & scripts (keyed by document_id)
_texts: dict[str, str] = {}
# Document IDs currently being parsed by an ingest job (avoids duplicate parse_file + vision calls)
_parsing_document_ids: set[str] = set()
PARSED_TEXTS_DIR = DATA_DIR / "parsed_texts"
_scripts: dict[str, list] = {}
_script_meta: dict[str, dict] = {}
_MAX_SCRIPT_VERSIONS = 24
_TTS_REWRITE_STREAM_TIMEOUT_SECONDS = 20 * 60
_ANCHOR_OFFSET_RE = re.compile(r":o(-?\d+):(-?\d+)", flags=re.IGNORECASE)
_ANCHOR_PAGE_RE = re.compile(r":p(-?\d+)", flags=re.IGNORECASE)
_ANCHOR_SLIDE_RE = re.compile(r":s(-?\d+)", flags=re.IGNORECASE)
_FULLTEXT_WINDOW_DEFAULT_CHARS = 90_000
_FULLTEXT_WINDOW_MAX_CHARS = 300_000


def _wipe_dir_contents(path: Path) -> int:
    """Delete all files and folders under path, keeping path itself."""
    if not path.exists():
        return 0
    removed = 0
    for p in list(path.iterdir()):
        try:
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def _normalise_script(script: list) -> list[dict]:
    """Convert script items to plain dicts {voice, text, grounding?} for JSON storage."""
    plain: list[dict] = []
    for item in script:
        if isinstance(item, DialogueLine):
            row = {"voice": item.voice, "text": item.text}
            grounding = str(item.grounding or "").strip()
            if grounding:
                row["grounding"] = grounding
            plain.append(row)
        elif isinstance(item, dict):
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            voice = str(item.get("voice", "host") or "host")
            row = {"voice": voice, "text": text}
            grounding = str(item.get("grounding", "") or "").strip()
            if grounding:
                row["grounding"] = grounding
            plain.append(row)
    return plain


def _utc_now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _script_char_count(script: list[dict]) -> int:
    return sum(len(str(row.get("text", "") or "")) for row in script or [])


def _script_hash(script: list[dict]) -> str:
    payload = json.dumps(script or [], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _format_confidence_meta(label: str, confidence: object, breakdown: object) -> str:
    parts: list[str] = []
    lbl = str(label or "").strip()
    if lbl:
        parts.append(lbl)
    if isinstance(confidence, (float, int)):
        parts.append(f"Надежность: {int(float(confidence) * 100)}%")
    src = breakdown if isinstance(breakdown, dict) else {}
    rq = src.get("retrieval_quality")
    ec = src.get("evidence_coverage")
    ag = src.get("answer_grounding")
    if isinstance(rq, (float, int)) and isinstance(ec, (float, int)) and isinstance(ag, (float, int)):
        parts.append(f"Поиск: {int(float(rq) * 100)}%")
        parts.append(f"Покрытие: {int(float(ec) * 100)}%")
        parts.append(f"Опора: {int(float(ag) * 100)}%")
    return " · ".join(parts).strip()


def _safe_id_token(value: object) -> str:
    token = "".join(
        ch if (ch.isascii() and (ch.isalnum() or ch in "._:-")) else "_"
        for ch in str(value or "").strip()
    )
    return token or "na"


def _build_chunk_evidence_id(document_id: str, chunk_id: str, chunk_index: object) -> str:
    doc = _safe_id_token(document_id)
    chunk = _safe_id_token(chunk_id)
    idx_token = str(chunk_index) if isinstance(chunk_index, int) and chunk_index >= 0 else "na"
    return f"ev:{doc}:{chunk}:{idx_token}"


def _build_chunk_anchor_id(document_id: str, chunk_id: str, row: dict, locator: dict) -> str:
    doc = _safe_id_token(document_id)
    chunk = _safe_id_token(chunk_id)
    start = locator.get("char_start")
    end = locator.get("char_end")
    if isinstance(start, int) and start >= 0:
        length = max(1, int(end) - start) if isinstance(end, int) else 1
        return f"a:{doc}:{chunk}:o{start}:{length}"
    page = locator.get("page")
    if isinstance(page, int):
        return f"a:{doc}:{chunk}:p{page}"
    slide = locator.get("slide")
    if isinstance(slide, int):
        return f"a:{doc}:{chunk}:s{slide}"

    anchor_seed = str(
        row.get("anchor")
        or row.get("section_path")
        or row.get("caption")
        or row.get("text")
        or chunk_id
    )
    digest = hashlib.sha1(anchor_seed.encode("utf-8", errors="ignore")).hexdigest()[:10]
    return f"a:{doc}:{chunk}:h{digest}"


def _apply_anchor_id_to_locator(anchor_id: str, locator: dict) -> dict:
    loc = dict(locator or {})
    aid = str(anchor_id or "").strip()
    if not aid:
        return loc
    loc.setdefault("anchor_id", aid)

    offset_m = _ANCHOR_OFFSET_RE.search(aid)
    if offset_m:
        start = _safe_int(offset_m.group(1), default=-1)
        length = _safe_int(offset_m.group(2), default=0)
        if start >= 0:
            loc["char_start"] = start
            if length > 0:
                loc["char_end"] = start + length

    page_m = _ANCHOR_PAGE_RE.search(aid)
    if page_m and loc.get("page") is None:
        page = _safe_int(page_m.group(1), default=-1)
        if page >= 0:
            loc["page"] = page

    slide_m = _ANCHOR_SLIDE_RE.search(aid)
    if slide_m and loc.get("slide") is None:
        slide = _safe_int(slide_m.group(1), default=-1)
        if slide >= 0:
            loc["slide"] = slide

    return loc


def _normalise_script_versions(raw) -> list[dict]:
    out: list[dict] = []
    seen_ids: set[str] = set()
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        script = _normalise_script(item.get("script") if isinstance(item.get("script"), list) else [])
        if not script:
            continue
        version_id = str(item.get("id") or "").strip() or f"v{uuid.uuid4().hex[:8]}"
        if version_id in seen_ids:
            continue
        created_at = str(item.get("created_at") or "").strip() or _utc_now_iso()
        reason = str(item.get("reason") or "").strip() or "update"
        note = str(item.get("note") or "").strip()
        line_count = len(script)
        chars = _script_char_count(script)
        digest = str(item.get("hash") or "").strip() or _script_hash(script)
        row = {
            "id": version_id,
            "created_at": created_at,
            "reason": reason,
            "line_count": line_count,
            "chars": chars,
            "hash": digest,
            "script": script,
        }
        if note:
            row["note"] = note
        out.append(row)
        seen_ids.add(version_id)
    if len(out) > _MAX_SCRIPT_VERSIONS:
        out = out[-_MAX_SCRIPT_VERSIONS:]
    return out


def _script_line_word_count(text: str) -> int:
    return len([t for t in str(text or "").replace("\n", " ").split() if t.strip()])


def _normalise_script_lock_indexes(raw, *, script_len: int | None = None) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    if not isinstance(raw, list):
        return out
    max_idx = None
    if isinstance(script_len, int) and script_len > 0:
        max_idx = script_len - 1
    for item in raw:
        try:
            idx = int(item)
        except (TypeError, ValueError):
            continue
        if idx < 0:
            continue
        if max_idx is not None and idx > max_idx:
            continue
        if idx in seen:
            continue
        seen.add(idx)
        out.append(idx)
    out.sort()
    return out


def _parse_voice_document_ids_form(default_document_id: str, raw_document_ids: str | None) -> list[str]:
    fallback = str(default_document_id or "").strip()
    raw = str(raw_document_ids or "").strip()
    if not raw:
        return [fallback] if fallback else []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            items = parsed
        else:
            items = [parsed]
    except json.JSONDecodeError:
        items = [x.strip() for x in raw.split(",")]
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        key = str(item or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    if not out and fallback:
        out.append(fallback)
    return out


def _parsed_text_path(document_id: str) -> Path:
    return PARSED_TEXTS_DIR / f"{document_id}.txt"


def _load_parsed_text_from_disk(document_id: str) -> str | None:
    path = _parsed_text_path(document_id)
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return None


def _save_parsed_text_to_disk(document_id: str, text: str) -> None:
    PARSED_TEXTS_DIR.mkdir(parents=True, exist_ok=True)
    path = _parsed_text_path(document_id)
    path.write_text(text or "", encoding="utf-8")


def _load_document_text(document_id: str) -> str:
    """Return cached, disk-cached, or freshly parsed document text. Do not call while document is in _parsing_document_ids."""
    text = _texts.get(document_id)
    if text is None:
        text = _load_parsed_text_from_disk(document_id)
        if text is not None:
            _texts[document_id] = text
            return str(text)
        file_path = _find_document_input_path(document_id)
        if not file_path or not file_path.exists():
            raise HTTPException(404, "Исходный файл документа не найден")
        try:
            text = ingest_service.parse_file(file_path)
        except ValueError as e:
            raise HTTPException(400, str(e))
        _texts[document_id] = text
        _save_parsed_text_to_disk(document_id, text)
    return str(text or "")


def _extract_anchor_offset(anchor_id: str) -> int | None:
    aid = str(anchor_id or "").strip()
    if not aid:
        return None
    match = _ANCHOR_OFFSET_RE.search(aid)
    if not match:
        return None
    start = _safe_int(match.group(1), default=-1)
    return start if start >= 0 else None


def _find_highlight_offset(text: str, highlight: str) -> int | None:
    body = str(text or "")
    q = str(highlight or "").strip()
    if not body or not q:
        return None

    exact_idx = body.lower().find(q.lower())
    if exact_idx >= 0:
        return exact_idx

    words = [re.escape(tok) for tok in re.sub(r"\s+", " ", q).split(" ") if tok]
    if not words:
        return None
    loose_pattern = re.compile(r"\s+".join(words), flags=re.IGNORECASE)
    m = loose_pattern.search(body)
    if not m:
        return None
    return int(m.start())


def _clamp_slice(total: int, start: int, end: int) -> tuple[int, int]:
    safe_total = max(0, int(total))
    safe_start = max(0, min(safe_total, int(start)))
    safe_end = max(safe_start, min(safe_total, int(end)))
    return safe_start, safe_end


def _resolve_fulltext_window(
    text: str,
    *,
    start: int | None,
    end: int | None,
    around: int | None,
    anchor_id: str,
    highlight: str,
    max_chars: int,
    full: bool,
) -> tuple[int, int, str]:
    body = str(text or "")
    total = len(body)
    window_limit = max(1, min(int(max_chars or _FULLTEXT_WINDOW_DEFAULT_CHARS), _FULLTEXT_WINDOW_MAX_CHARS))
    if full or total <= window_limit:
        return 0, total, "full"

    if start is not None or end is not None:
        raw_start = int(start or 0)
        raw_end = int(end) if end is not None else raw_start + window_limit
        if raw_end <= raw_start:
            raw_end = raw_start + window_limit
        sl_start, sl_end = _clamp_slice(total, raw_start, raw_end)
        return sl_start, sl_end, "range"

    center = None
    reason = "head"
    if around is not None:
        center = int(around)
        reason = "around"
    if center is None:
        anchor_offset = _extract_anchor_offset(anchor_id)
        if anchor_offset is not None:
            center = int(anchor_offset)
            reason = "anchor"
    if center is None:
        highlight_offset = _find_highlight_offset(body, highlight)
        if highlight_offset is not None:
            center = int(highlight_offset)
            reason = "highlight"
    if center is None:
        center = 0

    center = max(0, min(total, int(center)))
    half = window_limit // 2
    sl_start = max(0, center - half)
    sl_end = min(total, sl_start + window_limit)
    if sl_end - sl_start < window_limit:
        sl_start = max(0, sl_end - window_limit)
    sl_start, sl_end = _clamp_slice(total, sl_start, sl_end)
    return sl_start, sl_end, reason


def _ensure_documents_exist(document_ids: list[str]) -> None:
    missing = [doc_id for doc_id in document_ids if not document_store.get_document(doc_id)]
    if missing:
        if len(missing) == 1:
            raise HTTPException(404, f"Документ не найден: {missing[0]}")
        raise HTTPException(404, {"message": "Часть документов не найдена", "missing_document_ids": missing})


def _find_document_input_path(document_id: str) -> Path | None:
    candidates = sorted(INPUTS_DIR.glob(f"{document_id}.*"))
    if not candidates:
        return None
    return candidates[0]


def _compute_file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as src:
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest().lower()


def _find_duplicate_document_by_hash(file_hash: str) -> dict | None:
    digest = str(file_hash or "").strip().lower()
    if not digest:
        return None
    direct = document_store.find_document_by_file_hash(digest)
    if direct:
        return direct

    for doc in document_store.list_documents():
        document_id = str(doc.get("document_id") or "").strip()
        if not document_id:
            continue
        existing_hash = str(doc.get("file_hash") or "").strip().lower()
        if existing_hash == digest:
            return doc
        if existing_hash:
            continue
        source_path = _find_document_input_path(document_id)
        if not source_path or not source_path.exists() or not source_path.is_file():
            continue
        try:
            computed = _compute_file_sha256(source_path)
        except OSError:
            continue
        document_store.update_document(document_id, file_hash=computed)
        if computed == digest:
            refreshed = document_store.get_document(document_id)
            return refreshed or doc
    return None


def _get_script_meta(document_id: str) -> dict:
    cached = _script_meta.get(document_id)
    if isinstance(cached, dict):
        return dict(cached)
    doc = document_store.get_document(document_id) or {}
    raw = doc.get("script_meta")
    meta = dict(raw) if isinstance(raw, dict) else {}
    _script_meta[document_id] = dict(meta)
    return meta


def _save_script_meta(document_id: str, meta: dict) -> dict:
    clean_meta = dict(meta or {})
    _script_meta[document_id] = dict(clean_meta)
    document_store.update_document(document_id, script_meta=clean_meta)
    return clean_meta


def _get_script_locks(document_id: str, *, script_len: int | None = None) -> list[int]:
    meta = _get_script_meta(document_id)
    return _normalise_script_lock_indexes(meta.get("locks"), script_len=script_len)


def _save_script_locks(document_id: str, locks, *, script_len: int | None = None) -> list[int]:
    meta = _get_script_meta(document_id)
    norm_locks = _normalise_script_lock_indexes(locks, script_len=script_len)
    meta["locks"] = norm_locks
    _save_script_meta(document_id, meta)
    return norm_locks


def _load_document_script(document_id: str) -> list[dict]:
    script = _scripts.get(document_id)
    if isinstance(script, list) and script:
        norm = _normalise_script(script)
        _scripts[document_id] = norm
        return norm
    doc = document_store.get_document(document_id) or {}
    doc_script = doc.get("script")
    if isinstance(doc_script, list) and doc_script:
        norm = _normalise_script(doc_script)
        _scripts[document_id] = norm
        return norm
    return []


def _ensure_script_versions(document_id: str, script_plain: list[dict]) -> tuple[list[dict], str | None]:
    meta = _get_script_meta(document_id)
    versions = _normalise_script_versions(meta.get("versions"))
    current_version_id = str(meta.get("current_version_id") or "").strip() or None
    changed = False

    if not versions and script_plain:
        first_id = f"v{uuid.uuid4().hex[:8]}"
        versions = [{
            "id": first_id,
            "created_at": _utc_now_iso(),
            "reason": "bootstrap",
            "note": "Авто-снимок текущего скрипта",
            "line_count": len(script_plain),
            "chars": _script_char_count(script_plain),
            "hash": _script_hash(script_plain),
            "script": [dict(row) for row in script_plain],
        }]
        current_version_id = first_id
        changed = True

    valid_ids = {str(v.get("id")) for v in versions}
    if versions and current_version_id not in valid_ids:
        current_version_id = str(versions[-1].get("id"))
        changed = True

    locks = _normalise_script_lock_indexes(meta.get("locks"), script_len=len(script_plain) if script_plain else None)
    if locks != _normalise_script_lock_indexes(meta.get("locks")):
        changed = True

    if changed:
        meta["versions"] = versions
        meta["locks"] = locks
        if current_version_id:
            meta["current_version_id"] = current_version_id
        else:
            meta.pop("current_version_id", None)
        _script_meta[document_id] = dict(meta)
        document_store.update_document(document_id, script_meta=meta)
    return versions, current_version_id


def _save_script_with_version(
    document_id: str,
    script,
    *,
    reason: str,
    note: str | None = None,
    force_new_version: bool = False,
) -> list[dict]:
    script_plain = _normalise_script(script if isinstance(script, list) else [])
    if not script_plain:
        raise ValueError("Скрипт пустой")

    meta = _get_script_meta(document_id)
    versions = _normalise_script_versions(meta.get("versions"))
    digest = _script_hash(script_plain)
    now_iso = _utc_now_iso()
    note_text = str(note or "").strip()

    same_as_latest = (
        len(versions) > 0
        and str(versions[-1].get("hash") or "") == digest
        and _safe_int(versions[-1].get("line_count"), 0) == len(script_plain)
    )

    if same_as_latest and not force_new_version:
        current_version_id = str(versions[-1].get("id"))
    else:
        current_version_id = f"v{uuid.uuid4().hex[:8]}"
        entry = {
            "id": current_version_id,
            "created_at": now_iso,
            "reason": str(reason or "update").strip() or "update",
            "line_count": len(script_plain),
            "chars": _script_char_count(script_plain),
            "hash": digest,
            "script": [dict(row) for row in script_plain],
        }
        if note_text:
            entry["note"] = note_text
        versions.append(entry)
        if len(versions) > _MAX_SCRIPT_VERSIONS:
            versions = versions[-_MAX_SCRIPT_VERSIONS:]

    locks = _normalise_script_lock_indexes(meta.get("locks"), script_len=len(script_plain))
    meta["locks"] = locks
    meta["versions"] = versions
    meta["current_version_id"] = current_version_id

    _scripts[document_id] = script_plain
    _script_meta[document_id] = dict(meta)
    document_store.update_document(document_id, script=script_plain, script_meta=meta)
    return script_plain


def _version_public_row(version: dict, *, index: int, is_current: bool) -> dict:
    return {
        "version_id": str(version.get("id")),
        "label": f"v{index}",
        "created_at": str(version.get("created_at") or ""),
        "reason": str(version.get("reason") or ""),
        "note": str(version.get("note") or ""),
        "line_count": _safe_int(version.get("line_count"), 0),
        "chars": _safe_int(version.get("chars"), 0),
        "hash": str(version.get("hash") or ""),
        "is_current": bool(is_current),
    }


def _find_script_version(versions: list[dict], version_id: str) -> dict | None:
    key = str(version_id or "").strip()
    if not key:
        return None
    for version in versions:
        if str(version.get("id") or "") == key:
            return version
    return None


def _diff_script_versions(left: list[dict], right: list[dict], *, max_items: int = 80) -> dict:
    left_rows = _normalise_script(left if isinstance(left, list) else [])
    right_rows = _normalise_script(right if isinstance(right, list) else [])
    total = max(len(left_rows), len(right_rows))
    added = 0
    removed = 0
    changed = 0
    changed_lines: list[dict] = []
    for idx in range(total):
        left_row = left_rows[idx] if idx < len(left_rows) else None
        right_row = right_rows[idx] if idx < len(right_rows) else None
        if left_row is None and right_row is not None:
            added += 1
            change_type = "added"
        elif left_row is not None and right_row is None:
            removed += 1
            change_type = "removed"
        elif left_row == right_row:
            continue
        else:
            changed += 1
            change_type = "modified"
        if len(changed_lines) < max_items:
            changed_lines.append({
                "line": idx + 1,
                "change_type": change_type,
                "left": left_row,
                "right": right_row,
            })
    unchanged = max(0, min(len(left_rows), len(right_rows)) - changed)
    return {
        "left_lines": len(left_rows),
        "right_lines": len(right_rows),
        "added": added,
        "removed": removed,
        "changed": changed,
        "unchanged": unchanged,
        "changes": changed_lines,
    }


def _format_script_neighbors(script: list[dict], line_index: int, *, window: int = 2) -> tuple[str, str]:
    before_parts: list[str] = []
    after_parts: list[str] = []
    start = max(0, int(line_index) - max(0, int(window)))
    stop = min(len(script), int(line_index) + max(0, int(window)) + 1)
    for i in range(start, stop):
        if i == line_index:
            continue
        row = script[i] if 0 <= i < len(script) else {}
        item = f"{i + 1}. {row.get('voice', 'host')}: {str(row.get('text', '')).strip()}"
        if i < line_index:
            before_parts.append(item)
        else:
            after_parts.append(item)
    return ("\n".join(before_parts) or "(нет)"), ("\n".join(after_parts) or "(нет)")


def _parse_batch_run_params(body: dict) -> dict:
    mode = str(body.get("mode", "audio")).strip().lower()
    if mode not in {"audio", "script_audio"}:
        raise HTTPException(400, "mode должен быть одним из: audio, script_audio")

    raw_ids = body.get("document_ids")
    if not isinstance(raw_ids, list):
        raise HTTPException(400, "document_ids должен быть массивом")
    document_ids = [str(x).strip() for x in raw_ids if str(x).strip()]
    if not document_ids:
        raise HTTPException(400, "Список document_ids пуст")

    minutes = int(body.get("minutes", 5) or 5)
    style = str(body.get("style", "conversational") or "conversational")
    voices = body.get("voices")
    if not isinstance(voices, list) or not voices:
        voices = ["host", "guest1", "guest2"]
    scenario = str(body.get("scenario", "classic_overview") or "classic_overview")
    scenario_options = body.get("scenario_options")
    if not isinstance(scenario_options, dict):
        scenario_options = {}
    generation_mode = str(body.get("generation_mode", "single_pass") or "single_pass")
    role_llm_map = body.get("role_llm_map")
    if not isinstance(role_llm_map, dict):
        role_llm_map = None
    focus = str(body.get("focus", "") or "").strip() or None
    tts_friendly = bool(body.get("tts_friendly", True))

    return {
        "mode": mode,
        "document_ids": document_ids,
        "minutes": minutes,
        "style": style,
        "voices": voices,
        "scenario": scenario,
        "scenario_options": scenario_options,
        "generation_mode": generation_mode,
        "role_llm_map": role_llm_map,
        "focus": focus,
        "tts_friendly": tts_friendly,
    }


async def _enqueue_audio_job(document_id: str, background_tasks: BackgroundTasks, *, parent_job_id: str | None = None) -> str:
    script = _scripts.get(document_id)
    if not script:
        doc = document_store.get_document(document_id)
        doc_script = (doc or {}).get("script") if doc else None
        if isinstance(doc_script, list) and doc_script:
            script = _normalise_script(doc_script)
            _scripts[document_id] = script
    if not script:
        raise HTTPException(400, "Сначала сгенерируйте скрипт подкаста (кнопка «Сгенерировать скрипт»)")

    job_id = await job_manager.create_job_with_meta(
        lane="audio",
        job_type="audio",
        recipe={"document_id": document_id},
        parent_job_id=parent_job_id,
    )

    async def _task():
        async def _progress(p: int):
            await job_manager.raise_if_cancel_requested(job_id)
            await job_manager.update_job(job_id, progress=p)

        await job_manager.raise_if_cancel_requested(job_id)
        mp3_path = await tts_synthesise_script(script, document_id, progress_cb=_progress)
        await job_manager.raise_if_cancel_requested(job_id)
        return [str(mp3_path)]

    background_tasks.add_task(job_manager.run_job, job_id, _task(), lane="audio")
    return job_id


async def _enqueue_batch_job(batch_params: dict, background_tasks: BackgroundTasks, *, parent_job_id: str | None = None) -> str:
    job_id = await job_manager.create_job_with_meta(
        lane="batch",
        job_type="batch",
        recipe=dict(batch_params),
        parent_job_id=parent_job_id,
    )

    async def _task():
        mode = str(batch_params["mode"])
        document_ids = list(batch_params["document_ids"])
        total = len(document_ids)
        outputs: list[str] = []
        report_rows: list[dict] = []

        for i, document_id in enumerate(document_ids):
            await job_manager.raise_if_cancel_requested(job_id)
            row: dict = {"document_id": document_id, "mode": mode, "status": "ok"}
            step_start = int((i / total) * 100)
            step_end = int(((i + 1) / total) * 100)
            await job_manager.update_job(job_id, progress=min(step_start, 99))
            try:
                if mode == "script_audio":
                    script = await podcast_service.generate_podcast_script(
                        document_id=document_id,
                        minutes=int(batch_params["minutes"]),
                        style=str(batch_params["style"]),
                        focus=batch_params.get("focus"),
                        voices=list(batch_params["voices"]),
                        scenario=str(batch_params["scenario"]),
                        scenario_options=dict(batch_params["scenario_options"]),
                        generation_mode=str(batch_params["generation_mode"]),
                        role_llm_map=batch_params.get("role_llm_map"),
                        tts_friendly=bool(batch_params["tts_friendly"]),
                    )
                    script_plain = _save_script_with_version(
                        document_id,
                        script,
                        reason="generate",
                        note="Batch script_audio",
                    )
                    row["script_lines"] = len(script_plain)

                script_plain = _scripts.get(document_id)
                if not script_plain:
                    doc = document_store.get_document(document_id)
                    doc_script = (doc or {}).get("script") if doc else None
                    if isinstance(doc_script, list) and doc_script:
                        script_plain = _normalise_script(doc_script)
                        _scripts[document_id] = script_plain
                if not script_plain:
                    raise RuntimeError("Нет скрипта для генерации аудио")

                async def _progress_local(p: int):
                    await job_manager.raise_if_cancel_requested(job_id)
                    overall = int(step_start + ((step_end - step_start) * (max(0, min(100, p)) / 100.0)))
                    await job_manager.update_job(job_id, progress=min(overall, 99))

                mp3_path = await tts_synthesise_script(script_plain, document_id, progress_cb=_progress_local)
                outputs.append(str(mp3_path))
                row["audio_file"] = str(mp3_path)
            except job_manager.JobCancelledError:
                raise
            except Exception as e:
                row["status"] = "error"
                row["error"] = str(e)
            finally:
                report_rows.append(row)
                await job_manager.update_job(job_id, progress=min(step_end, 99))

        report = {
            "job_id": job_id,
            "mode": mode,
            "total": total,
            "ok": sum(1 for r in report_rows if r.get("status") == "ok"),
            "error": sum(1 for r in report_rows if r.get("status") == "error"),
            "items": report_rows,
        }
        report_path = OUTPUTS_DIR / f"{job_id}_batch_report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        outputs.append(str(report_path))
        return outputs

    background_tasks.add_task(job_manager.run_job, job_id, _task(), lane="batch")
    return job_id


# ---------- Documents (list / open / delete) --------------------------------

@router.get("/documents")
async def list_documents():
    """Список сохранённых документов (для открытия обработанных)."""
    docs = document_store.list_documents()
    # Return lightweight list: omit large summary/sources/script for list view
    return [
        {
            "document_id": d["document_id"],
            "filename": d["filename"],
            "created_at": d.get("created_at"),
            "ingested": d.get("ingested", False),
            "chunks": d.get("chunks", 0),
            "has_summary": bool(d.get("summary")),
            "has_script": bool(d.get("script")),
        }
        for d in docs
    ]


@router.get("/documents/{document_id}")
async def get_document(document_id: str):
    """Открыть документ: вернуть метаданные и восстановить скрипт в памяти."""
    doc = document_store.get_document(document_id)
    if not doc:
        raise HTTPException(404, "Документ не найден")
    # Restore script in memory so audio generation/export works
    script = doc.get("script") or []
    _scripts[document_id] = script
    if isinstance(doc.get("script_meta"), dict):
        _script_meta[document_id] = dict(doc.get("script_meta") or {})
    return doc


@router.get("/documents/{document_id}/source")
async def get_document_source_file(document_id: str, download: bool = Query(False), preview: bool = Query(False)):
    """Return source file or generated preview PDF for document preview/download."""
    doc = document_store.get_document(document_id)
    if not doc:
        raise HTTPException(404, "Документ не найден")
    file_path = _find_document_input_path(document_id)
    if not file_path or not file_path.exists():
        raise HTTPException(404, "Исходный файл документа не найден")
    served_path = file_path
    if bool(preview) and not bool(download):
        try:
            served_path = ingest_service.ensure_preview_pdf(file_path, document_id=document_id)
        except ValueError:
            served_path = file_path
    media_type = mimetypes.guess_type(str(served_path))[0] or "application/octet-stream"
    original_name = str(doc.get("filename") or "").strip()
    download_name = Path(original_name).name if original_name else ""
    if not download_name or download_name in {"", ".", "/"}:
        download_name = f"{document_id}{served_path.suffix}"
    if served_path != file_path and served_path.suffix.lower() == ".pdf":
        download_name = f"{Path(download_name).stem}.pdf"
    elif served_path.suffix and not download_name.lower().endswith(served_path.suffix.lower()):
        download_name = f"{document_id}{served_path.suffix}"
    safe_download_name = (
        download_name.replace('"', "")
        .replace("\n", "")
        .replace("\r", "")
    )
    ascii_download_name = "".join(ch for ch in safe_download_name if 32 <= ord(ch) < 127 and ch not in {'"', ";", "\\"}).strip()
    if not ascii_download_name or not any(ch.isalnum() for ch in ascii_download_name):
        ascii_download_name = f"{document_id}{served_path.suffix}"
    is_inline_previewable = (
        media_type == "application/pdf"
        or media_type.startswith("image/")
        or media_type.startswith("text/")
        or media_type in {"application/json", "application/xml", "application/xhtml+xml"}
    )
    disposition_type = "attachment" if bool(download) or not is_inline_previewable else "inline"
    encoded_download_name = quote(safe_download_name, safe="")
    content_disposition = (
        f'{disposition_type}; filename="{ascii_download_name}"; '
        f"filename*=UTF-8''{encoded_download_name}"
    )
    return FileResponse(
        path=str(served_path),
        media_type=media_type,
        headers={"Content-Disposition": content_disposition},
    )


@router.get("/documents/{document_id}/fulltext")
async def get_document_fulltext(
    document_id: str,
    start: int | None = Query(None, ge=0),
    end: int | None = Query(None, ge=0),
    around: int | None = Query(None, ge=0),
    max_chars: int = Query(_FULLTEXT_WINDOW_DEFAULT_CHARS, ge=4_000, le=_FULLTEXT_WINDOW_MAX_CHARS),
    anchor_id: str = Query(""),
    highlight: str = Query(""),
    full: bool = Query(False),
):
    """Return parsed document text, optionally as a windowed range for source viewer."""
    doc = document_store.get_document(document_id)
    if not doc:
        raise HTTPException(404, "Документ не найден")

    # Wait if ingest is currently parsing this document to avoid duplicate vision API calls
    wait_end = asyncio.get_event_loop().time() + 600.0
    while document_id in _parsing_document_ids and asyncio.get_event_loop().time() < wait_end:
        await asyncio.sleep(0.5)
    if document_id in _parsing_document_ids:
        raise HTTPException(503, "Документ ещё индексируется, повторите запрос через минуту")

    text = _load_document_text(document_id)
    total = len(text)
    slice_start, slice_end, reason = _resolve_fulltext_window(
        text,
        start=start,
        end=end,
        around=around,
        anchor_id=anchor_id,
        highlight=highlight,
        max_chars=max_chars,
        full=full,
    )
    payload_text = text[slice_start:slice_end]
    is_windowed = slice_start > 0 or slice_end < total

    return {
        "document_id": document_id,
        "text": payload_text,
        "total_chars": total,
        "start": slice_start,
        "end": slice_end,
        "is_windowed": is_windowed,
        "has_more_before": slice_start > 0,
        "has_more_after": slice_end < total,
        "window_reason": reason,
        "window_limit": int(max_chars),
    }


@router.get("/documents/{document_id}/chunks/{chunk_id}")
async def get_document_chunk(document_id: str, chunk_id: str, highlight: str = "", anchor_id: str = ""):
    """Resolve one chunk with full text and source locator for UI source-highlighting."""
    doc = document_store.get_document(document_id)
    if not doc:
        raise HTTPException(404, "Документ не найден")
    row = rag_service.get_chunk(document_id, chunk_id)
    if not row:
        raise HTTPException(404, "Фрагмент не найден")

    locator = dict(row.get("source_locator") or {})
    source_path = _find_document_input_path(document_id)
    if source_path:
        ext = str(source_path.suffix or "").strip().lower().lstrip(".")
        locator.setdefault("file_extension", ext or None)
        locator.setdefault("source_filename", source_path.name)
        if not locator.get("kind"):
            if ext == "pdf":
                locator["kind"] = "pdf"
            elif ext in {"doc", "docx"}:
                locator["kind"] = "docx"
            elif ext == "pptx":
                locator["kind"] = "pptx"
            elif ext in {"txt", "md", "rst"}:
                locator["kind"] = "text"
            elif ext in {"html", "htm"}:
                locator["kind"] = "html"

    highlight_text = str(highlight or "").strip()
    if highlight_text:
        locator = rag_service.build_source_locator(
            chunk_id=str(row.get("chunk_id", "")),
            chunk_index=row.get("chunk_index"),
            text=str(row.get("text", "") or ""),
            page=row.get("page"),
            section_path=row.get("section_path"),
            anchor=row.get("anchor"),
            caption=row.get("caption"),
            source_type=row.get("source_type"),
            highlight_hint=highlight_text,
        ) | {
            "file_extension": locator.get("file_extension"),
            "source_filename": locator.get("source_filename"),
        }
    if anchor_id:
        locator = _apply_anchor_id_to_locator(anchor_id, locator)

    resolved_chunk_id = str(row.get("chunk_id") or chunk_id)
    resolved_chunk_index = row.get("chunk_index")
    evidence_id = _build_chunk_evidence_id(document_id, resolved_chunk_id, resolved_chunk_index)
    resolved_anchor_id = str(anchor_id or "").strip() or _build_chunk_anchor_id(document_id, resolved_chunk_id, row, locator)
    locator_with_anchor = dict(locator)
    locator_with_anchor.setdefault("anchor_id", resolved_anchor_id)

    return {
        "evidence_id": evidence_id,
        "anchor_id": resolved_anchor_id,
        "document_id": document_id,
        "chunk_id": resolved_chunk_id,
        "chunk_index": resolved_chunk_index,
        "text": row.get("text", ""),
        "page": row.get("page"),
        "section_path": row.get("section_path"),
        "anchor": row.get("anchor"),
        "caption": row.get("caption"),
        "source_type": row.get("source_type"),
        "source_locator": locator_with_anchor,
        "source_url": f"/api/documents/{document_id}/source",
    }


@router.delete("/documents/{document_id}")
async def delete_document(document_id: str):
    """Удалить документ из базы, памяти, RAG и файлов."""
    doc = document_store.get_document(document_id)
    if not doc:
        raise HTTPException(404, "Документ не найден")
    document_store.delete_document(document_id)
    project_store.remove_document_from_all_projects(document_id)
    _texts.pop(document_id, None)
    _scripts.pop(document_id, None)
    _script_meta.pop(document_id, None)
    rag_service.delete_document_index(document_id)
    parsed_path = _parsed_text_path(document_id)
    if parsed_path.exists():
        try:
            parsed_path.unlink()
        except OSError:
            pass
    for p in INPUTS_DIR.glob(f"{document_id}.*"):
        try:
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink()
        except OSError:
            pass
    for p in OUTPUTS_DIR.glob(f"{document_id}*"):
        try:
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink()
        except OSError:
            pass
    await job_manager.remove_document_artifacts(document_id)
    return {"ok": True}


# ---------- Projects / Collections -----------------------------------------

@router.get("/projects")
async def list_projects():
    rows = project_store.list_projects()
    return [
        {
            "project_id": p.get("project_id"),
            "name": p.get("name"),
            "created_at": p.get("created_at"),
            "updated_at": p.get("updated_at"),
            "document_count": len(p.get("document_ids") or []),
            "document_ids": p.get("document_ids") or [],
        }
        for p in rows
    ]


@router.get("/projects/{project_id}")
async def get_project(project_id: str):
    row = project_store.get_project(project_id)
    if not row:
        raise HTTPException(404, "Подборка не найдена")
    return row


@router.post("/projects")
async def create_project(body: dict):
    name = str(body.get("name", "") or "").strip()
    if not name:
        raise HTTPException(400, "Укажите название подборки")
    raw_ids = body.get("document_ids")
    doc_ids = raw_ids if isinstance(raw_ids, list) else []
    project_id = uuid.uuid4().hex[:12]
    try:
        row = project_store.create_project(project_id, name, doc_ids)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return row


@router.put("/projects/{project_id}")
async def update_project(project_id: str, body: dict):
    name = body.get("name") if "name" in body else None
    raw_ids = body.get("document_ids") if "document_ids" in body else None
    doc_ids = raw_ids if isinstance(raw_ids, list) else ([] if "document_ids" in body else None)
    raw_settings = body.get("settings") if "settings" in body else None
    if "settings" in body and raw_settings is not None and not isinstance(raw_settings, dict):
        raise HTTPException(400, "settings должен быть объектом")
    settings = raw_settings if isinstance(raw_settings, dict) else ({} if "settings" in body else None)
    try:
        row = project_store.update_project(project_id, name=name, document_ids=doc_ids, settings=settings)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not row:
        raise HTTPException(404, "Подборка не найдена")
    return row


@router.delete("/projects/{project_id}")
async def delete_project(project_id: str):
    ok = project_store.delete_project(project_id)
    if not ok:
        raise HTTPException(404, "Подборка не найдена")
    return {"ok": True}


@router.get("/projects/{project_id}/notebook")
async def get_project_notebook(project_id: str):
    row = project_store.get_project_notebook(project_id)
    if not row:
        raise HTTPException(404, "Подборка не найдена")
    return row


@router.get("/projects/{project_id}/settings")
async def get_project_settings(project_id: str):
    row = project_store.get_project_settings(project_id)
    if not row:
        raise HTTPException(404, "Подборка не найдена")
    return row


@router.put("/projects/{project_id}/settings")
async def set_project_settings(project_id: str, body: dict):
    settings = body.get("settings")
    if settings is None:
        raise HTTPException(400, "В теле запроса нужен ключ settings")
    if not isinstance(settings, dict):
        raise HTTPException(400, "settings должен быть объектом")
    row = project_store.set_project_settings(project_id, settings)
    if not row:
        raise HTTPException(404, "Подборка не найдена")
    return row


@router.put("/projects/{project_id}/notes")
async def set_project_notes(project_id: str, body: dict):
    notes = body.get("notes")
    if notes is None:
        raise HTTPException(400, "В теле запроса нужен ключ notes")
    row = project_store.set_project_notes(project_id, str(notes))
    if not row:
        raise HTTPException(404, "Подборка не найдена")
    return row


@router.post("/projects/{project_id}/pins")
async def add_project_pin(project_id: str, body: dict):
    answer = str(body.get("answer") or "").strip()
    if not answer:
        raise HTTPException(400, "Нельзя закрепить пустой ответ")
    pin_id = str(body.get("pin_id") or "").strip() or uuid.uuid4().hex[:12]
    question = str(body.get("question") or "").strip()
    citations = body.get("citations") if isinstance(body.get("citations"), list) else []
    mode = str(body.get("mode") or "").strip() or None
    meta = str(body.get("meta") or "").strip()
    try:
        row = project_store.add_project_pin(
            project_id,
            pin_id=pin_id,
            question=question,
            answer=answer,
            citations=citations,
            mode=mode,
            meta=meta,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not row:
        raise HTTPException(404, "Подборка не найдена")
    return row


@router.delete("/projects/{project_id}/pins/{pin_id}")
async def delete_project_pin(project_id: str, pin_id: str):
    if not str(pin_id or "").strip():
        raise HTTPException(400, "pin_id обязателен")
    ok = project_store.delete_project_pin(project_id, pin_id)
    if not ok:
        raise HTTPException(404, "Закреплённый ответ не найден")
    return {"ok": True}


@router.post("/import_script")
async def import_script(body: dict):
    """Импорт скрипта без документа: создать виртуальный документ и сразу перейти к генерации."""
    raw = body.get("script")
    if not isinstance(raw, list):
        raise HTTPException(400, "В теле запроса нужен массив script: [{ voice, text }, ...]")
    script = []
    for item in raw:
        if not isinstance(item, dict) or "text" not in item:
            continue
        row = {"voice": item.get("voice", "host"), "text": str(item["text"])}
        grounding = str(item.get("grounding", "") or "").strip()
        if grounding:
            row["grounding"] = grounding
        script.append(row)
    if not script:
        raise HTTPException(400, "В скрипте должен быть хотя бы один элемент с полем text")
    document_id = uuid.uuid4().hex[:12]
    document_store.add_document(document_id, "Импорт скрипта")
    try:
        script_plain = _save_script_with_version(
            document_id,
            script,
            reason="import",
            note="Импорт скрипта без исходного документа",
            force_new_version=True,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"document_id": document_id, "script": script_plain}


# ---------- A) Upload ------------------------------------------------------

@router.post("/upload", response_model=UploadResponse)
async def upload_file(file: UploadFile = File(...)):
    document_id = uuid.uuid4().hex[:12]
    ext = Path(file.filename or "file.bin").suffix
    dest = INPUTS_DIR / f"{document_id}{ext}"
    hasher = hashlib.sha256()
    with dest.open("wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
            out.write(chunk)
    await file.close()
    digest = hasher.hexdigest().lower()
    duplicate_doc = _find_duplicate_document_by_hash(digest)
    if duplicate_doc:
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            pass
        duplicate_id = str(duplicate_doc.get("document_id") or "").strip()
        duplicate_name = str(duplicate_doc.get("filename") or file.filename or "unknown").strip() or "unknown"
        duplicate_ingested = bool(duplicate_doc.get("ingested"))
        detail = "Документ уже загружен"
        if duplicate_ingested:
            detail += " и был открыт из базы."
        else:
            detail += "; открыт существующий экземпляр."
        return UploadResponse(
            document_id=duplicate_id,
            filename=duplicate_name,
            duplicate=True,
            duplicate_of=duplicate_id,
            existing_ingested=duplicate_ingested,
            message=detail,
        )
    document_store.add_document(document_id, file.filename or "unknown", file_hash=digest)
    return UploadResponse(document_id=document_id, filename=file.filename or "unknown")


@router.post("/upload_url", response_model=UploadResponse)
async def upload_url(body: dict):
    url = body.get("url", "")
    if not url:
        raise HTTPException(400, "Укажите URL")
    document_id = uuid.uuid4().hex[:12]
    try:
        text = ingest_service.parse_url(url)
    except ValueError as e:
        raise HTTPException(400, str(e))
    dest = INPUTS_DIR / f"{document_id}.txt"
    dest.write_text(text, encoding="utf-8")
    _texts[document_id] = text
    document_store.add_document(document_id, url)
    return UploadResponse(document_id=document_id, filename=url)


# ---------- B) Ingest ------------------------------------------------------

@router.post("/ingest/{document_id}", response_model=IngestResponse)
async def ingest(document_id: str):
    # Find file
    candidates = list(INPUTS_DIR.glob(f"{document_id}.*"))
    if not candidates:
        raise HTTPException(404, "Документ не найден")
    file_path = candidates[0]

    # Parse if not already
    if document_id not in _texts:
        try:
            text = ingest_service.parse_file(file_path)
        except ValueError as e:
            raise HTTPException(400, str(e))
        _texts[document_id] = text
    else:
        text = _texts[document_id]

    # Chunk & index
    chunks = rag_service.chunk_text(text)
    if not chunks:
        raise HTTPException(400, "Не удалось извлечь текст для индексации: документ пустой или не распознан.")
    n = rag_service.index_document(document_id, chunks)
    document_store.update_document(document_id, ingested=True, chunks=n)
    return IngestResponse(document_id=document_id, chunks=n)


@router.post("/ingest/{document_id}/job")
async def ingest_job(document_id: str, background_tasks: BackgroundTasks):
    # Validate source upfront
    candidates = list(INPUTS_DIR.glob(f"{document_id}.*"))
    if not candidates:
        raise HTTPException(404, "Документ не найден")

    job_id = await job_manager.create_job()

    async def _task():
        await job_manager.update_job(job_id, progress=3, progress_message=None)
        file_path = candidates[0]
        # Mark as parsing so fulltext endpoint does not start a second parse_file (duplicate vision calls)
        _parsing_document_ids.add(document_id)
        try:
            # Always re-parse on ingest job so vision runs again and progress is shown (no cache)
            _texts.pop(document_id, None)

            parse_progress_q: Queue[tuple[int, int]] = Queue()

            def _vision_progress(current: int, total: int) -> None:
                parse_progress_q.put((current, total))

            try:
                parse_fut = asyncio.create_task(
                    asyncio.to_thread(ingest_service.parse_file, file_path, progress_cb=_vision_progress)
                )
                while not parse_fut.done():
                    try:
                        cur, tot = parse_progress_q.get_nowait()
                        if tot and tot > 0:
                            p = 3 + int(9 * cur / tot)
                            msg = f"Обработка изображений: {cur}/{tot}"
                        else:
                            p = 3 + min(8, cur)
                            msg = f"Обработка изображений: {cur}…" if cur else None
                        await job_manager.update_job(
                            job_id,
                            progress=max(3, min(12, p)),
                            progress_message=msg,
                        )
                    except Empty:
                        pass
                    await asyncio.sleep(0.25)
                while True:
                    try:
                        cur, tot = parse_progress_q.get_nowait()
                        if tot and tot > 0:
                            msg = f"Обработка изображений: {cur}/{tot}"
                        else:
                            msg = f"Обработка изображений: {cur}…" if cur else None
                        await job_manager.update_job(job_id, progress=12, progress_message=msg)
                    except Empty:
                        break
                text = await parse_fut
            except ValueError as e:
                raise HTTPException(400, str(e))
            _texts[document_id] = text
            _save_parsed_text_to_disk(document_id, text)

            await job_manager.update_job(job_id, progress=12, progress_message=None)
            chunks = rag_service.chunk_text(text)
            if not chunks:
                raise HTTPException(400, "Не удалось извлечь текст для индексации: документ пустой или не распознан.")

            progress_q: Queue[tuple[int, int]] = Queue()

            def _progress(done: int, total: int) -> None:
                progress_q.put((done, total))

            async def _index_and_track() -> int:
                fut = asyncio.create_task(
                    asyncio.to_thread(
                        rag_service.index_document_with_progress,
                        document_id,
                        chunks,
                        batch_size=32,
                        progress_cb=_progress,
                    )
                )
                while not fut.done():
                    try:
                        done, total = progress_q.get_nowait()
                        frac = done / max(1, total)
                        p = 12 + int(frac * 84)
                        await job_manager.update_job(job_id, progress=max(12, min(96, p)))
                    except Empty:
                        pass
                    await asyncio.sleep(0.2)

                # flush pending events
                while True:
                    try:
                        done, total = progress_q.get_nowait()
                        frac = done / max(1, total)
                        p = 12 + int(frac * 84)
                        await job_manager.update_job(job_id, progress=max(12, min(96, p)))
                    except Empty:
                        break
                return await fut

            n = await _index_and_track()
            document_store.update_document(document_id, ingested=True, chunks=n)
            await job_manager.update_job(job_id, progress=100, progress_message=None)
            return [f"chunks:{n}"]
        finally:
            _parsing_document_ids.discard(document_id)

    background_tasks.add_task(job_manager.run_job, job_id, _task(), lane="ingest")
    return {"job_id": job_id}


# ---------- C) Summary -----------------------------------------------------

@router.get("/summary/{document_id}", response_model=SummaryResponse)
async def summary(document_id: str):
    try:
        text, sources = await podcast_service.generate_summary(document_id)
    except LMStudioError as e:
        raise HTTPException(503, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    document_store.update_document(document_id, summary=text, sources=[s.model_dump() for s in sources])
    return SummaryResponse(document_id=document_id, summary=text, sources=sources)


@router.get("/summary/{document_id}/stream")
async def summary_stream(document_id: str):
    """Stream summary from LM Studio (SSE); save at end. Events: data: {\"chunk\": \"...\"} then data: {\"done\": true, \"full\": \"...\"}."""
    try:
        system, user, chunks = podcast_service._summary_prompts(document_id)
    except ValueError as e:
        raise HTTPException(400, str(e))

    async def event_stream():
        full = ""
        try:
            async for chunk in chat_completion_stream(system, user, temperature=0.3):
                full += chunk
                yield f"data: {json.dumps({'chunk': chunk}, ensure_ascii=False)}\n\n"
            full = podcast_service.clean_summary_output(full)
            sources = [
                {"chunk_id": c["chunk_id"], "text": c["text"][:200] + ("..." if len(c["text"]) > 200 else "")}
                for c in chunks[:6]
            ]
            document_store.update_document(document_id, summary=full, sources=sources)
            yield f"data: {json.dumps({'done': True, 'full': full, 'sources': sources}, ensure_ascii=False)}\n\n"
        except LMStudioError as e:
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------- C2) Voice Q&A --------------------------------------------------

@router.websocket("/voice_wake/ws")
async def voice_wake_ws(websocket: WebSocket):
    await websocket.accept()
    wake_word = "Гена"
    try:
        raw_qs = str(websocket.query_params.get("wake_word", "Гена") or "Гена").strip()
        if raw_qs:
            wake_word = raw_qs
    except Exception:
        pass

    try:
        session = wake_word_service.create_session(wake_word)
    except RuntimeError as e:
        await websocket.send_json({"type": "error", "message": str(e)})
        await websocket.close(code=1011)
        return

    await websocket.send_json(
        {
            "type": "ready",
            "sample_rate": 16000,
            "channels": 1,
            "encoding": "pcm_s16le",
            "wake_word": str(session.get("wake_word") or "гена"),
        }
    )

    try:
        while True:
            msg = await websocket.receive()
            msg_type = msg.get("type")
            if msg_type == "websocket.disconnect":
                break
            if msg_type != "websocket.receive":
                continue
            data_bytes = msg.get("bytes")
            data_text = msg.get("text")
            if isinstance(data_bytes, (bytes, bytearray)) and data_bytes:
                event = wake_word_service.process_audio_chunk(session, bytes(data_bytes))
                if event:
                    await websocket.send_json(event)
                continue
            if isinstance(data_text, str) and data_text.strip():
                try:
                    payload = json.loads(data_text)
                except json.JSONDecodeError:
                    continue
                if not isinstance(payload, dict):
                    continue
                msg_kind = str(payload.get("type", "") or "").strip().lower()
                if msg_kind == "ping":
                    await websocket.send_json({"type": "pong"})
                    continue
                if msg_kind in {"set_wake_word", "config"}:
                    next_wake = str(payload.get("wake_word", "") or "").strip() or wake_word
                    wake_word = next_wake
                    session = wake_word_service.create_session(wake_word)
                    await websocket.send_json(
                        {
                            "type": "config_applied",
                            "wake_word": str(session.get("wake_word") or "гена"),
                        }
                    )
    except WebSocketDisconnect:
        return
    except Exception as e:
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
        try:
            await websocket.close(code=1011)
        except Exception:
            pass


@router.post("/voice_qa/{document_id}/stream")
async def voice_qa_stream(
    request: Request,
    document_id: str,
    audio: UploadFile = File(...),
    document_ids: str = Form(""),
    strict_sources: bool = Form(False),
    use_summary_context: bool = Form(False),
    question_mode: str = Form("default"),
    answer_length: str = Form("medium"),
    knowledge_mode: str = Form("document_only"),
    stt_model: str = Form(""),
    chat_mode: str = Form("qa"),
    thread_id: str = Form("main-chat"),
    history_limit: int = Form(12),
    with_tts: bool = Form(True),
):
    qa_document_ids = _parse_voice_document_ids_form(document_id, document_ids)
    if not qa_document_ids:
        raise HTTPException(400, "Укажите document_id или document_ids")
    _ensure_documents_exist(qa_document_ids)

    filename = audio.filename or "question.webm"
    try:
        audio_bytes = await audio.read()
    finally:
        await audio.close()
    if not audio_bytes:
        raise HTTPException(400, "Пустой аудиофайл вопроса")

    chat_mode_norm = str(chat_mode or "qa").strip().lower()
    if chat_mode_norm not in {"qa", "conv"}:
        raise HTTPException(400, "chat_mode должен быть 'qa' или 'conv'")
    stt_model_norm = voice_qa_service.normalize_stt_model_name(stt_model)
    thread_id_norm = str(thread_id or "main-chat").strip() or "main-chat"
    history_limit_norm = max(1, min(int(history_limit or 12), 40))
    history = chat_store.get_history(thread_id_norm, limit=history_limit_norm) if chat_mode_norm == "conv" else []

    def _sse(obj: dict) -> str:
        return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

    async def event_stream():
        question_text = ""
        answer_text = ""
        sources = []
        confidence = None
        mode_key = None
        audio_filename = None
        audio_duration_sec = None
        tts_started = False
        tts_segment_index = 0
        tts_segments_emitted = 0
        tts_buffer = ""
        tts_sources_section_seen = False

        async def _synth_stream_tts_segment(segment_text: str) -> str | None:
            try:
                return await voice_qa_service.synth_answer_tts_segment(
                    document_id,
                    segment_text,
                    tts_segment_index,
                )
            except asyncio.TimeoutError as e:
                raise VoiceQaStageError(
                    "tts",
                    f"Озвучка ответа не успела завершиться (таймаут {voice_qa_service.TTS_TIMEOUT_SECONDS} с).",
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

        try:
            yield _sse({"status": "stt_start", "stage": "stt", "stt_model": stt_model_norm})
            stt_partial_queue: Queue[str] = Queue()
            last_stt_partial = ""

            def _on_stt_partial(partial_text: str) -> None:
                text = str(partial_text or "").strip()
                if not text:
                    return
                try:
                    stt_partial_queue.put_nowait(text)
                except Exception:
                    pass

            try:
                stt_task = asyncio.create_task(
                    voice_qa_service.transcribe_audio_streaming(
                        audio_bytes,
                        filename,
                        on_partial=_on_stt_partial,
                        stt_model=stt_model_norm,
                    )
                )
                while not stt_task.done():
                    if await request.is_disconnected():
                        stt_task.cancel()
                        try:
                            await stt_task
                        except BaseException:
                            pass
                        return
                    try:
                        while True:
                            partial_text = stt_partial_queue.get_nowait()
                            if partial_text and partial_text != last_stt_partial:
                                last_stt_partial = partial_text
                                yield _sse(
                                    {
                                        "status": "stt_partial",
                                        "stage": "stt",
                                        "partial_text": partial_text,
                                    }
                                )
                    except Empty:
                        pass
                    await asyncio.sleep(0.05)

                try:
                    while True:
                        partial_text = stt_partial_queue.get_nowait()
                        if partial_text and partial_text != last_stt_partial:
                            last_stt_partial = partial_text
                            yield _sse(
                                {
                                    "status": "stt_partial",
                                    "stage": "stt",
                                    "partial_text": partial_text,
                                }
                            )
                except Empty:
                    pass

                question_text, duration = await stt_task
                audio_duration_sec = round(float(duration), 2)
            except asyncio.TimeoutError as e:
                raise VoiceQaStageError(
                    "stt",
                    f"STT не успел распознать вопрос (таймаут {voice_qa_service.STT_TIMEOUT_SECONDS} с).",
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

            if len(question_text) > voice_qa_service.MAX_QUESTION_CHARS:
                raise VoiceQaStageError(
                    "stt",
                    (
                        "Распознанный вопрос слишком длинный "
                        f"({len(question_text)} символов). Максимум: {voice_qa_service.MAX_QUESTION_CHARS}."
                    ),
                    status_code=400,
                    retryable=False,
                    code="question_too_long",
                    hint="Задайте вопрос короче или разделите его на несколько.",
                )

            yield _sse(
                {
                    "status": "stt_done",
                    "stage": "stt",
                    "question_text": question_text,
                    "audio_duration_sec": audio_duration_sec,
                    "chat_mode": chat_mode_norm,
                    "stt_model": stt_model_norm,
                }
            )
            if await request.is_disconnected():
                return

            try:
                if chat_mode_norm == "conv":
                    system, user, sources, confidence, confidence_breakdown, _effective_knowledge_mode = podcast_service.build_conversational_qa_payload(
                        qa_document_ids,
                        question_text,
                        history,
                        strict_sources=bool(strict_sources),
                        use_summary_context=bool(use_summary_context),
                        question_mode=(question_mode or "default"),
                        answer_length=(answer_length or "medium"),
                        knowledge_mode=(knowledge_mode or "document_only"),
                    )
                else:
                    system, user, sources, confidence, confidence_breakdown, _effective_knowledge_mode = podcast_service.build_qa_payload(
                        qa_document_ids,
                        question_text,
                        strict_sources=bool(strict_sources),
                        use_summary_context=bool(use_summary_context),
                        question_mode=(question_mode or "default"),
                        answer_length=(answer_length or "medium"),
                        knowledge_mode=(knowledge_mode or "document_only"),
                    )
            except ValueError as e:
                raise VoiceQaStageError(
                    "rag",
                    str(e),
                    status_code=400,
                    retryable=False,
                    code="rag_validation",
                    hint="Проверьте индексацию документа или отключите строгий режим.",
                ) from e

            yield _sse({"status": "llm_start", "stage": "llm"})
            answer_len_key, answer_len_cfg = podcast_service.resolve_answer_length(answer_length)
            mode_key = str(question_mode or "default").strip().lower() or "default"
            if mode_key not in {"default", "quote", "overview", "formulas"}:
                mode_key = "default"
            try:
                async with asyncio.timeout(voice_qa_service.QA_TIMEOUT_SECONDS):
                    async for chunk in chat_completion_stream(
                        system,
                        user,
                        temperature=0.2,
                        max_tokens=int(answer_len_cfg.get("max_tokens", 2200)),
                    ):
                        if await request.is_disconnected():
                            return
                        answer_text += chunk
                        yield _sse({"chunk": chunk, "stage": "llm"})
                        if with_tts and not tts_sources_section_seen:
                            tts_buffer += chunk
                            ready_segments, tts_buffer, tts_sources_section_seen = voice_qa_service.split_stream_tts_segments(
                                tts_buffer,
                                final=False,
                            )
                            for seg_text in ready_segments:
                                if await request.is_disconnected():
                                    return
                                if not tts_started:
                                    tts_started = True
                                    yield _sse({"status": "tts_start", "stage": "tts", "mode": "segments"})
                                tts_segment_index += 1
                                seg_filename = await _synth_stream_tts_segment(seg_text)
                                if not seg_filename:
                                    continue
                                tts_segments_emitted += 1
                                yield _sse(
                                    {
                                        "status": "tts_chunk_ready",
                                        "stage": "tts",
                                        "audio_filename": seg_filename,
                                        "segment_index": tts_segment_index,
                                        "segmented": True,
                                    }
                                )
            except asyncio.TimeoutError as e:
                raise VoiceQaStageError(
                    "llm",
                    f"Генерация ответа превысила таймаут ({voice_qa_service.QA_TIMEOUT_SECONDS} с).",
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

            yield _sse({"status": "llm_done", "stage": "llm"})
            if await request.is_disconnected():
                return

            if with_tts and answer_text.strip():
                if not tts_sources_section_seen and tts_buffer:
                    ready_segments, tts_buffer, tts_sources_section_seen = voice_qa_service.split_stream_tts_segments(
                        tts_buffer,
                        final=True,
                    )
                    for seg_text in ready_segments:
                        if await request.is_disconnected():
                            return
                        if not tts_started:
                            tts_started = True
                            yield _sse({"status": "tts_start", "stage": "tts", "mode": "segments"})
                        tts_segment_index += 1
                        seg_filename = await _synth_stream_tts_segment(seg_text)
                        if not seg_filename:
                            continue
                        tts_segments_emitted += 1
                        yield _sse(
                            {
                                "status": "tts_chunk_ready",
                                "stage": "tts",
                                "audio_filename": seg_filename,
                                "segment_index": tts_segment_index,
                                "segmented": True,
                            }
                        )
                if tts_started:
                    yield _sse(
                        {
                            "status": "tts_done",
                            "stage": "tts",
                            "segmented": True,
                            "segments": tts_segments_emitted,
                        }
                    )

            result = {
                "document_id": document_id,
                "document_ids": qa_document_ids,
                "question_text": question_text,
                "answer_text": answer_text,
                "sources": sources or [],
                "confidence": confidence,
                "confidence_breakdown": confidence_breakdown,
                "mode": mode_key,
                "chat_mode": chat_mode_norm,
                "audio_filename": audio_filename,
                "audio_duration_sec": audio_duration_sec,
                "answer_length": answer_len_key,
                "stt_model": stt_model_norm,
            }

            if not await request.is_disconnected():
                meta = ""
                if isinstance(confidence, (float, int)):
                    label = "Voice Conv RAG" if chat_mode_norm == "conv" else "Voice Q&A"
                    meta = _format_confidence_meta(label, confidence, confidence_breakdown)
                chat_store.append_messages(
                    thread_id_norm,
                    [
                        {"role": "user", "text": question_text},
                        {
                            "role": "assistant",
                            "text": answer_text,
                            "meta": meta,
                            "citations": sources or [],
                        },
                    ],
                )
                yield _sse({"done": True, **result})
        except VoiceQaStageError as e:
            detail = e.to_detail()
            yield _sse({"error": detail.get("message") or str(e), **detail})
        except Exception as e:
            yield _sse(
                {
                    "error": str(e) or "Неизвестная ошибка voice streaming",
                    "type": "voice_qa_error",
                    "stage": "unknown",
                    "message": str(e) or "Неизвестная ошибка voice streaming",
                    "code": "voice_qa_stream_unknown",
                    "retryable": True,
                    "hint": "Попробуйте повторить запрос.",
                }
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/voice_qa/{document_id}", response_model=VoiceQaResponse)
async def voice_qa(
    request: Request,
    document_id: str,
    audio: UploadFile = File(...),
    document_ids: str = Form(""),
    strict_sources: bool = Form(False),
    use_summary_context: bool = Form(False),
    question_mode: str = Form("default"),
    answer_length: str = Form("medium"),
    knowledge_mode: str = Form("document_only"),
    stt_model: str = Form(""),
    chat_mode: str = Form("qa"),
    thread_id: str = Form("main-chat"),
    history_limit: int = Form(12),
    with_tts: bool = Form(True),
):
    qa_document_ids = _parse_voice_document_ids_form(document_id, document_ids)
    if not qa_document_ids:
        raise HTTPException(400, "Укажите document_id или document_ids")
    _ensure_documents_exist(qa_document_ids)

    filename = audio.filename or "question.webm"
    try:
        audio_bytes = await audio.read()
    finally:
        await audio.close()

    if not audio_bytes:
        raise HTTPException(400, "Пустой аудиофайл вопроса")

    chat_mode_norm = str(chat_mode or "qa").strip().lower()
    if chat_mode_norm not in {"qa", "conv"}:
        raise HTTPException(400, "chat_mode должен быть 'qa' или 'conv'")
    stt_model_norm = voice_qa_service.normalize_stt_model_name(stt_model)
    thread_id_norm = str(thread_id or "main-chat").strip() or "main-chat"
    history_limit_norm = max(1, min(int(history_limit or 12), 40))
    history = chat_store.get_history(thread_id_norm, limit=history_limit_norm) if chat_mode_norm == "conv" else []

    try:
        result = await voice_qa_service.run_voice_qa(
            document_id=document_id,
            document_ids=qa_document_ids,
            audio_bytes=audio_bytes,
            filename=filename,
            strict_sources=bool(strict_sources),
            use_summary_context=bool(use_summary_context),
            question_mode=(question_mode or "default"),
            answer_length=(answer_length or "medium"),
            knowledge_mode=(knowledge_mode or "document_only"),
            chat_mode=chat_mode_norm,
            history=history,
            with_tts=bool(with_tts),
            stt_model=stt_model_norm,
        )
    except VoiceQaStageError as e:
        raise HTTPException(e.status_code, e.to_detail())
    except asyncio.TimeoutError:
        raise HTTPException(
            504,
            {
                "type": "voice_qa_error",
                "stage": "unknown",
                "message": "Voice Q&A превысил общий таймаут обработки",
                "code": "voice_qa_timeout",
                "retryable": True,
                "hint": "Попробуйте более короткий вопрос или режим ответа 'Короткий'.",
            },
        )
    except LMStudioError as e:
        raise HTTPException(503, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(503, str(e))

    if await request.is_disconnected():
        return result

    meta = ""
    if isinstance(result.get("confidence"), (float, int)):
        label = "Voice Conv RAG" if chat_mode_norm == "conv" else "Voice Q&A"
        meta = _format_confidence_meta(label, result.get("confidence"), result.get("confidence_breakdown"))
    chat_store.append_messages(
        thread_id_norm,
        [
            {"role": "user", "text": result.get("question_text", "")},
            {
                "role": "assistant",
                "text": result.get("answer_text", ""),
                "meta": meta,
                "citations": result.get("sources") or [],
            },
        ],
    )
    return result


@router.post("/chat/query")
async def chat_query(body: dict):
    question = str(body.get("question", "") or "").strip()
    if not question:
        raise HTTPException(400, "Вопрос не должен быть пустым")
    raw_ids = body.get("document_ids")
    if isinstance(raw_ids, list):
        document_ids = [str(x).strip() for x in raw_ids if str(x).strip()]
    else:
        single = str(body.get("document_id", "") or "").strip()
        document_ids = [single] if single else []
    thread_id = str(body.get("thread_id", "") or "main-chat").strip() or "main-chat"
    strict_sources = bool(body.get("strict_sources", False))
    use_summary_context = bool(body.get("use_summary_context", False))
    question_mode = str(body.get("question_mode", "") or "").strip() or None
    answer_length = str(body.get("answer_length", "") or "medium").strip() or "medium"
    knowledge_mode = str(body.get("knowledge_mode", "") or "document_only").strip() or "document_only"
    if not document_ids:
        raise HTTPException(400, "Укажите document_id или document_ids")
    try:
        result = await podcast_service.answer_question(
            document_ids,
            question,
            strict_sources=strict_sources,
            use_summary_context=use_summary_context,
            question_mode=question_mode,
            answer_length=answer_length,
            knowledge_mode=knowledge_mode,
        )
        meta = _format_confidence_meta("", result.get("confidence"), result.get("confidence_breakdown"))
        chat_store.append_messages(
            thread_id,
            [
                {"role": "user", "text": question},
                {
                    "role": "assistant",
                    "text": result.get("answer", ""),
                    "meta": meta,
                    "citations": result.get("citations") or [],
                },
            ],
        )
        return result
    except LMStudioError as e:
        raise HTTPException(503, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/chat/query/stream")
async def chat_query_stream(body: dict):
    question = str(body.get("question", "") or "").strip()
    if not question:
        raise HTTPException(400, "Вопрос не должен быть пустым")
    raw_ids = body.get("document_ids")
    if isinstance(raw_ids, list):
        document_ids = [str(x).strip() for x in raw_ids if str(x).strip()]
    else:
        single = str(body.get("document_id", "") or "").strip()
        document_ids = [single] if single else []
    thread_id = str(body.get("thread_id", "") or "main-chat").strip() or "main-chat"
    strict_sources = bool(body.get("strict_sources", False))
    use_summary_context = bool(body.get("use_summary_context", False))
    question_mode = str(body.get("question_mode", "") or "").strip() or None
    answer_length = str(body.get("answer_length", "") or "medium").strip() or "medium"
    knowledge_mode = str(body.get("knowledge_mode", "") or "document_only").strip() or "document_only"
    if not document_ids:
        raise HTTPException(400, "Укажите document_id или document_ids")
    try:
        system, user, citations, confidence, confidence_breakdown, effective_knowledge_mode = podcast_service.build_qa_payload(
            document_ids,
            question,
            strict_sources=strict_sources,
            use_summary_context=use_summary_context,
            question_mode=question_mode,
            answer_length=answer_length,
            knowledge_mode=knowledge_mode,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    _answer_len_key, answer_len_cfg = podcast_service.resolve_answer_length(answer_length)
    async def event_stream():
        full = ""
        try:
            async for chunk in chat_completion_stream(
                system,
                user,
                temperature=0.2,
                max_tokens=int(answer_len_cfg.get("max_tokens", 2200)),
            ):
                full += chunk
                yield f"data: {json.dumps({'chunk': chunk}, ensure_ascii=False)}\n\n"
            meta = _format_confidence_meta("", confidence, confidence_breakdown)
            chat_store.append_messages(
                thread_id,
                [
                    {"role": "user", "text": question},
                    {"role": "assistant", "text": full, "meta": meta, "citations": citations},
                ],
            )
            yield f"data: {json.dumps({'done': True, 'full': full, 'confidence': confidence, 'confidence_breakdown': confidence_breakdown, 'citations': citations, 'answer_length': _answer_len_key, 'knowledge_mode': podcast_service._normalize_knowledge_mode(knowledge_mode), 'effective_knowledge_mode': effective_knowledge_mode, 'has_model_knowledge_content': podcast_service._answer_has_model_knowledge_content(full)}, ensure_ascii=False)}\n\n"
        except LMStudioError as e:
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/chat/query/conversational")
async def chat_query_conversational(body: dict):
    question = str(body.get("question", "") or "").strip()
    if not question:
        raise HTTPException(400, "Вопрос не должен быть пустым")
    raw_ids = body.get("document_ids")
    if isinstance(raw_ids, list):
        document_ids = [str(x).strip() for x in raw_ids if str(x).strip()]
    else:
        single = str(body.get("document_id", "") or "").strip()
        document_ids = [single] if single else []
    thread_id = str(body.get("thread_id", "") or "main-chat").strip() or "main-chat"
    strict_sources = bool(body.get("strict_sources", False))
    use_summary_context = bool(body.get("use_summary_context", False))
    question_mode = str(body.get("question_mode", "") or "").strip() or None
    answer_length = str(body.get("answer_length", "") or "medium").strip() or "medium"
    knowledge_mode = str(body.get("knowledge_mode", "") or "document_only").strip() or "document_only"
    if not document_ids:
        raise HTTPException(400, "Укажите document_id или document_ids")
    history = chat_store.get_history(thread_id, limit=40)
    try:
        result = await podcast_service.answer_question_conversational(
            document_ids,
            question,
            history,
            strict_sources=strict_sources,
            use_summary_context=use_summary_context,
            question_mode=question_mode,
            answer_length=answer_length,
            knowledge_mode=knowledge_mode,
        )
        meta = _format_confidence_meta("Conversational RAG", result.get("confidence"), result.get("confidence_breakdown"))
        chat_store.append_messages(
            thread_id,
            [
                {"role": "user", "text": question},
                {
                    "role": "assistant",
                    "text": result.get("answer", ""),
                    "meta": meta,
                    "citations": result.get("citations") or [],
                },
            ],
        )
        return result
    except LMStudioError as e:
        raise HTTPException(503, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/chat/query/conversational/stream")
async def chat_query_conversational_stream(body: dict):
    question = str(body.get("question", "") or "").strip()
    if not question:
        raise HTTPException(400, "Вопрос не должен быть пустым")
    raw_ids = body.get("document_ids")
    if isinstance(raw_ids, list):
        document_ids = [str(x).strip() for x in raw_ids if str(x).strip()]
    else:
        single = str(body.get("document_id", "") or "").strip()
        document_ids = [single] if single else []
    thread_id = str(body.get("thread_id", "") or "main-chat").strip() or "main-chat"
    strict_sources = bool(body.get("strict_sources", False))
    use_summary_context = bool(body.get("use_summary_context", False))
    question_mode = str(body.get("question_mode", "") or "").strip() or None
    answer_length = str(body.get("answer_length", "") or "medium").strip() or "medium"
    knowledge_mode = str(body.get("knowledge_mode", "") or "document_only").strip() or "document_only"
    if not document_ids:
        raise HTTPException(400, "Укажите document_id или document_ids")
    history = chat_store.get_history(thread_id, limit=40)
    try:
        system, user, citations, confidence, confidence_breakdown, effective_knowledge_mode = podcast_service.build_conversational_qa_payload(
            document_ids,
            question,
            history,
            strict_sources=strict_sources,
            use_summary_context=use_summary_context,
            question_mode=question_mode,
            answer_length=answer_length,
            knowledge_mode=knowledge_mode,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    _answer_len_key, answer_len_cfg = podcast_service.resolve_answer_length(answer_length)
    async def event_stream():
        full = ""
        try:
            async for chunk in chat_completion_stream(
                system,
                user,
                temperature=0.2,
                max_tokens=int(answer_len_cfg.get("max_tokens", 2200)),
            ):
                full += chunk
                yield f"data: {json.dumps({'chunk': chunk}, ensure_ascii=False)}\n\n"
            meta = _format_confidence_meta("Conversational RAG", confidence, confidence_breakdown)
            chat_store.append_messages(
                thread_id,
                [
                    {"role": "user", "text": question},
                    {"role": "assistant", "text": full, "meta": meta, "citations": citations},
                ],
            )
            yield f"data: {json.dumps({'done': True, 'full': full, 'confidence': confidence, 'confidence_breakdown': confidence_breakdown, 'citations': citations, 'answer_length': _answer_len_key, 'knowledge_mode': podcast_service._normalize_knowledge_mode(knowledge_mode), 'effective_knowledge_mode': effective_knowledge_mode, 'has_model_knowledge_content': podcast_service._answer_has_model_knowledge_content(full)}, ensure_ascii=False)}\n\n"
        except LMStudioError as e:
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/chat/history")
async def chat_history(thread_id: str = "main-chat", limit: int = 60):
    return {"thread_id": thread_id, "messages": chat_store.get_history(thread_id, limit=limit)}


@router.delete("/chat/history")
async def chat_history_clear(thread_id: str = "main-chat"):
    chat_store.clear_history(thread_id)
    return {"ok": True}


@router.post("/compare")
async def compare_docs(body: dict):
    raw_ids = body.get("document_ids")
    if not isinstance(raw_ids, list):
        raise HTTPException(400, "document_ids должен быть массивом")
    document_ids = [str(x).strip() for x in raw_ids if str(x).strip()]
    focus = str(body.get("focus", "") or "").strip()
    try:
        return await podcast_service.compare_documents(document_ids, focus)
    except LMStudioError as e:
        raise HTTPException(503, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---------- D) Podcast script ----------------------------------------------

@router.post("/podcast_script/{document_id}", response_model=PodcastScriptResponse)
async def podcast_script(document_id: str, body: PodcastScriptRequest):
    try:
        script = await podcast_service.generate_podcast_script(
            document_id,
            minutes=body.minutes,
            style=body.style,
            focus=body.focus,
            voices=body.voices,
            scenario=body.scenario,
            scenario_options=body.scenario_options,
            generation_mode=body.generation_mode,
            role_llm_map=body.role_llm_map,
            outline_plan=body.outline_plan,
            tts_friendly=body.tts_friendly,
            knowledge_mode=body.knowledge_mode,
        )
    except LMStudioError as e:
        raise HTTPException(503, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))

    script_plain = _save_script_with_version(
        document_id,
        script,
        reason="generate",
    )
    # Для ответа API возвращаем в типизированном виде
    return PodcastScriptResponse(
        document_id=document_id,
        script=[DialogueLine(**item) for item in script_plain],
        knowledge_mode=podcast_service._normalize_knowledge_mode(body.knowledge_mode),
        effective_knowledge_mode=podcast_service._effective_script_knowledge_mode(body.scenario, body.knowledge_mode),
    )


@router.post("/podcast_script/{document_id}/outline")
async def podcast_script_outline(document_id: str, body: PodcastScriptRequest):
    try:
        outline = await podcast_service.generate_podcast_script_outline(
            document_id,
            minutes=body.minutes,
            style=body.style,
            focus=body.focus,
            voices=body.voices,
            scenario=body.scenario,
            scenario_options=body.scenario_options,
            role_llm_map=body.role_llm_map,
            knowledge_mode=body.knowledge_mode,
        )
        debug = podcast_service.get_script_generation_debug(
            document_id,
            minutes=body.minutes,
            style=body.style,
            focus=body.focus,
            voices=body.voices,
            scenario=body.scenario,
            scenario_options=body.scenario_options,
            generation_mode="turn_taking",
            tts_friendly=body.tts_friendly,
            knowledge_mode=body.knowledge_mode,
        )
        return {"document_id": document_id, "outline": outline, "prompt_debug": debug}
    except LMStudioError as e:
        raise HTTPException(503, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/podcast_script/{document_id}/stream")
async def podcast_script_stream(document_id: str, body: PodcastScriptRequest):
    """Stream podcast script from LM Studio (SSE); parse and save at end. Events: data: {\"chunk\": \"...\"} then data: {\"done\": true, \"full\": \"...\", \"script\": [...]}."""
    generation_mode = podcast_service._normalize_generation_mode(body.generation_mode)
    if generation_mode == "turn_taking":
        # Turn-taking uses step-by-step generation; we stream per-line chunks instead of raw LMStudio SSE deltas.
        async def event_stream_turn_taking():
            raw_lines: list[DialogueLine] = []
            full = ""
            try:
                try:
                    prompt_debug = podcast_service.get_script_generation_debug(
                        document_id,
                        minutes=body.minutes,
                        style=body.style,
                        focus=body.focus,
                        voices=body.voices,
                        scenario=body.scenario,
                        scenario_options=body.scenario_options,
                        generation_mode="turn_taking",
                        tts_friendly=body.tts_friendly,
                        knowledge_mode=body.knowledge_mode,
                    )
                    yield f"data: {json.dumps({'status': 'prompt_debug', 'prompt_debug': prompt_debug}, ensure_ascii=False)}\n\n"
                except Exception:
                    # Diagnostics should not block generation.
                    pass
                async for line in podcast_service.iter_podcast_script_turn_taking(
                    document_id,
                    minutes=body.minutes,
                    style=body.style,
                    focus=body.focus,
                    voices=body.voices,
                    scenario=body.scenario,
                    scenario_options=body.scenario_options,
                    role_llm_map=body.role_llm_map,
                    outline_plan=body.outline_plan,
                    knowledge_mode=body.knowledge_mode,
                ):
                    raw_lines.append(line)
                    preview_chunk = f"{line.voice}: {line.text}\n"
                    full += preview_chunk
                    yield f"data: {json.dumps({'chunk': preview_chunk}, ensure_ascii=False)}\n\n"

                lines = raw_lines
                if body.tts_friendly:
                    yield f"data: {json.dumps({'status': 'tts_rewrite', 'message': 'Подготовка текста для TTS…'}, ensure_ascii=False)}\n\n"
                    try:
                        lines = await asyncio.wait_for(
                            podcast_service.rewrite_script_tts_second_pass(lines, body.voices or ["host", "guest1", "guest2"]),
                            timeout=_TTS_REWRITE_STREAM_TIMEOUT_SECONDS,
                        )
                    except asyncio.TimeoutError:
                        yield f"data: {json.dumps({'warning': 'TTS-перепись заняла слишком долго; использую исходный скрипт.'}, ensure_ascii=False)}\n\n"
                    for line in lines:
                        line.text = latin_to_russian_readable_keep_pauses(line.text)
                podcast_service.validate_script_completeness(
                    lines,
                    body.voices or ["host", "guest1", "guest2"],
                    minutes=body.minutes,
                    mode="turn_taking",
                )
                script_plain = _save_script_with_version(
                    document_id,
                    lines,
                    reason="generate_stream",
                    note="turn_taking",
                )
                script_for_json = _normalise_script(script_plain)
                yield f"data: {json.dumps({'done': True, 'full': full, 'script': script_for_json, 'knowledge_mode': podcast_service._normalize_knowledge_mode(body.knowledge_mode), 'effective_knowledge_mode': podcast_service._effective_script_knowledge_mode(body.scenario, body.knowledge_mode)}, ensure_ascii=False)}\n\n"
            except LMStudioError as e:
                yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
            except ValueError as e:
                yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
            except Exception as e:
                # Keep SSE channel semantic even on unexpected backend failures.
                yield f"data: {json.dumps({'error': f'Внутренняя ошибка turn-taking: {e}'}, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            event_stream_turn_taking(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    try:
        system, user, voice_list, prompt_debug = podcast_service._script_prompts(
            document_id,
            minutes=body.minutes,
            style=body.style,
            focus=body.focus,
            voices=body.voices,
            scenario=body.scenario,
            scenario_options=body.scenario_options,
            tts_friendly=body.tts_friendly,
            knowledge_mode=body.knowledge_mode,
            return_debug=True,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    try:
        podcast_service.validate_role_llm_map(voice_list, body.role_llm_map)
    except ValueError as e:
        raise HTTPException(400, str(e))
    try:
        _role_name, llm_override = await podcast_service.validate_primary_role_llm_preflight(voice_list, body.role_llm_map)
    except ValueError as e:
        raise HTTPException(400, str(e))

    async def event_stream():
        full = ""
        try:
            yield f"data: {json.dumps({'status': 'prompt_debug', 'prompt_debug': prompt_debug}, ensure_ascii=False)}\n\n"
            async for chunk in chat_completion_stream(
                system,
                user,
                temperature=0.6,
                max_tokens=8192,
                model=(llm_override or {}).get("model"),
                base_url=(llm_override or {}).get("base_url"),
            ):
                full += chunk
                yield f"data: {json.dumps({'chunk': chunk}, ensure_ascii=False)}\n\n"
            lines = podcast_service._parse_script_json(full, voice_list)
            podcast_service.validate_script_completeness(lines, voice_list, minutes=body.minutes, mode="single_pass")
            if body.tts_friendly:
                yield f"data: {json.dumps({'status': 'tts_rewrite', 'message': 'Подготовка текста для TTS…'}, ensure_ascii=False)}\n\n"
                try:
                    lines = await asyncio.wait_for(
                        podcast_service.rewrite_script_tts_second_pass(lines, voice_list),
                        timeout=_TTS_REWRITE_STREAM_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'warning': 'TTS-перепись заняла слишком долго; использую исходный скрипт.'}, ensure_ascii=False)}\n\n"
                for line in lines:
                    line.text = latin_to_russian_readable_keep_pauses(line.text)
            script_plain = _save_script_with_version(
                document_id,
                lines,
                reason="generate_stream",
                note="single_pass",
            )
            script_for_json = _normalise_script(script_plain)
            yield f"data: {json.dumps({'done': True, 'full': full, 'script': script_for_json, 'knowledge_mode': podcast_service._normalize_knowledge_mode(body.knowledge_mode), 'effective_knowledge_mode': podcast_service._effective_script_knowledge_mode(body.scenario, body.knowledge_mode)}, ensure_ascii=False)}\n\n"
        except LMStudioError as e:
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
        except ValueError as e:
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/podcast_script/{document_id}/import", response_model=PodcastScriptResponse)
async def import_podcast_script(document_id: str, body: dict):
    """Импорт скрипта подкаста из JSON. body: { "script": [ {"voice": "...", "text": "..."}, ... ] }"""
    raw = body.get("script")
    if not isinstance(raw, list):
        raise HTTPException(400, "В теле запроса нужен массив script: [{ voice, text }, ...]")
    script = []
    for item in raw:
        if not isinstance(item, dict) or "text" not in item:
            continue
        row = {"voice": item.get("voice", "host"), "text": str(item["text"])}
        grounding = str(item.get("grounding", "") or "").strip()
        if grounding:
            row["grounding"] = grounding
        script.append(row)
    if not script:
        raise HTTPException(400, "В скрипте должен быть хотя бы один элемент с полем text")

    script_plain = _save_script_with_version(
        document_id,
        script,
        reason="import",
    )
    return PodcastScriptResponse(
        document_id=document_id,
        script=[DialogueLine(**item) for item in script_plain],
        knowledge_mode="document_only",
        effective_knowledge_mode="document_only",
    )


@router.get("/podcast_script/{document_id}/versions")
async def list_podcast_script_versions(document_id: str):
    doc = document_store.get_document(document_id)
    if not doc:
        raise HTTPException(404, "Документ не найден")
    script_plain = _load_document_script(document_id)
    versions, current_version_id = _ensure_script_versions(document_id, script_plain)
    rows = [
        _version_public_row(version, index=i, is_current=str(version.get("id")) == str(current_version_id or ""))
        for i, version in enumerate(versions, start=1)
    ]
    return {
        "document_id": document_id,
        "current_version_id": current_version_id,
        "versions": rows,
    }


@router.get("/podcast_script/{document_id}/versions/{version_id}")
async def get_podcast_script_version(document_id: str, version_id: str):
    doc = document_store.get_document(document_id)
    if not doc:
        raise HTTPException(404, "Документ не найден")
    script_plain = _load_document_script(document_id)
    versions, current_version_id = _ensure_script_versions(document_id, script_plain)
    version = _find_script_version(versions, version_id)
    if not version:
        raise HTTPException(404, "Версия скрипта не найдена")
    idx = versions.index(version) + 1
    return {
        "document_id": document_id,
        "version": _version_public_row(version, index=idx, is_current=str(version.get("id")) == str(current_version_id or "")),
        "script": _normalise_script(version.get("script") or []),
    }


@router.post("/podcast_script/{document_id}/versions/compare")
async def compare_podcast_script_versions(document_id: str, body: dict):
    doc = document_store.get_document(document_id)
    if not doc:
        raise HTTPException(404, "Документ не найден")
    script_plain = _load_document_script(document_id)
    versions, current_version_id = _ensure_script_versions(document_id, script_plain)
    if not versions:
        raise HTTPException(404, "Версии скрипта не найдены")

    left_version_id = str(body.get("left_version_id") or "").strip()
    right_version_id = str(body.get("right_version_id") or "").strip()
    if not right_version_id and current_version_id:
        right_version_id = str(current_version_id)
    if not left_version_id and len(versions) >= 2:
        left_version_id = str(versions[-2].get("id") or "")
    elif not left_version_id and versions:
        left_version_id = str(versions[0].get("id") or "")
    if not right_version_id and versions:
        right_version_id = str(versions[-1].get("id") or "")

    left_version = _find_script_version(versions, left_version_id)
    right_version = _find_script_version(versions, right_version_id)
    if not left_version or not right_version:
        raise HTTPException(400, "Не удалось выбрать версии для сравнения")

    left_idx = versions.index(left_version) + 1
    right_idx = versions.index(right_version) + 1
    diff = _diff_script_versions(
        left_version.get("script") if isinstance(left_version.get("script"), list) else [],
        right_version.get("script") if isinstance(right_version.get("script"), list) else [],
        max_items=80,
    )
    return {
        "document_id": document_id,
        "left_version": _version_public_row(left_version, index=left_idx, is_current=str(left_version.get("id")) == str(current_version_id or "")),
        "right_version": _version_public_row(right_version, index=right_idx, is_current=str(right_version.get("id")) == str(current_version_id or "")),
        "diff": diff,
    }


@router.post("/podcast_script/{document_id}/versions/{version_id}/restore")
async def restore_podcast_script_version(document_id: str, version_id: str):
    doc = document_store.get_document(document_id)
    if not doc:
        raise HTTPException(404, "Документ не найден")
    script_plain = _load_document_script(document_id)
    versions, _current_version_id = _ensure_script_versions(document_id, script_plain)
    version = _find_script_version(versions, version_id)
    if not version:
        raise HTTPException(404, "Версия скрипта не найдена")
    try:
        restored_script = _save_script_with_version(
            document_id,
            version.get("script") if isinstance(version.get("script"), list) else [],
            reason="restore",
            note=f"restored_from={version_id}",
            force_new_version=True,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    meta = _get_script_meta(document_id)
    return {
        "document_id": document_id,
        "restored_from_version_id": str(version_id),
        "current_version_id": str(meta.get("current_version_id") or ""),
        "script": restored_script,
    }


@router.get("/podcast_script/{document_id}/locks")
async def get_podcast_script_locks(document_id: str):
    doc = document_store.get_document(document_id)
    if not doc:
        raise HTTPException(404, "Документ не найден")
    script = _load_document_script(document_id)
    locks = _get_script_locks(document_id, script_len=len(script))
    return {"document_id": document_id, "locks": locks}


@router.post("/podcast_script/{document_id}/locks")
async def save_podcast_script_locks(document_id: str, body: dict):
    doc = document_store.get_document(document_id)
    if not doc:
        raise HTTPException(404, "Документ не найден")
    script = _load_document_script(document_id)
    locks = _save_script_locks(document_id, body.get("locks"), script_len=len(script))
    return {"document_id": document_id, "locks": locks}


@router.get("/podcast_script/{document_id}/tts_quality")
async def podcast_script_tts_quality(document_id: str):
    script = _scripts.get(document_id)
    if not script:
        raise HTTPException(404, "Скрипт для документа не найден")
    return analyse_script(script)


@router.post("/podcast_script/{document_id}/preview_line")
async def podcast_script_preview_line(document_id: str, body: dict):
    """Синтез одной реплики для быстрого предпрослушивания."""
    voice = str(body.get("voice", "host") or "host").strip()
    text = str(body.get("text", "") or "").strip()
    if not text:
        raise HTTPException(400, "Пустой текст реплики")

    line = {
        "voice": voice,
        "text": latin_to_russian_readable_keep_pauses(text),
    }
    preview_id = f"{document_id}_preview_{uuid.uuid4().hex[:8]}"
    try:
        mp3_path = await asyncio.wait_for(
            tts_synthesise_script([line], preview_id, progress_cb=None, apply_music=False, apply_postprocess=False),
            timeout=90,
        )
    except asyncio.TimeoutError:
        raise HTTPException(504, "Предпрослушка не успела сгенерироваться (таймаут 90 с).")
    return {"filename": mp3_path.name}


@router.post("/podcast_script/{document_id}/regenerate_line")
async def podcast_script_regenerate_line(document_id: str, body: dict):
    """Regenerate one script line using current script context + document RAG context."""
    script = _scripts.get(document_id)
    if not script:
        doc = document_store.get_document(document_id)
        doc_script = (doc or {}).get("script") if doc else None
        if isinstance(doc_script, list) and doc_script:
            script = _normalise_script(doc_script)
            _scripts[document_id] = script
    if not script:
        raise HTTPException(404, "Скрипт для документа не найден")

    try:
        line_index = int(body.get("line_index"))
    except (TypeError, ValueError):
        raise HTTPException(400, "Нужен целочисленный line_index")
    if line_index < 0 or line_index >= len(script):
        raise HTTPException(400, f"line_index вне диапазона: 0..{max(0, len(script) - 1)}")

    row = script[line_index]
    role = str(row.get("voice", "host") or "host").strip() or "host"
    original_text = str(row.get("text", "") or "").strip()
    if not original_text:
        raise HTTPException(400, "Исходная реплика пуста")

    instruction = str(body.get("instruction", "") or "").strip()
    tts_friendly = bool(body.get("tts_friendly", True))
    neighbor_window = max(1, min(4, int(body.get("neighbor_window", 2) or 2)))
    doc_top_k = max(2, min(8, int(body.get("doc_top_k", 4) or 4)))

    before_ctx, after_ctx = _format_script_neighbors(script, line_index, window=neighbor_window)
    query_parts = [role, original_text]
    if before_ctx and before_ctx != "(нет)":
        query_parts.append(before_ctx)
    if after_ctx and after_ctx != "(нет)":
        query_parts.append(after_ctx)
    rag_query = " | ".join(q for q in query_parts if q)
    rag_rows = rag_service.retrieve(document_id, rag_query, top_k=doc_top_k)
    doc_context = podcast_service._compose_script_context(
        rag_rows,
        max_chars=2200,
        max_chunks=doc_top_k,
        per_chunk_chars=900,
    ) if rag_rows else ""

    target_words = max(10, min(120, _script_line_word_count(original_text)))
    system = (
        "Ты редактор подкаст-сценария. Перепиши только ОДНУ реплику в диалоге.\n"
        "Пиши только на русском языке.\n"
        "Опирайся на контекст документа и соседние реплики.\n"
        "Верни ТОЛЬКО новый текст реплики без JSON, без имени роли, без markdown."
    )
    instruction_block = f"Доп. инструкция пользователя: {instruction}\n\n" if instruction else ""
    user = (
        f"Номер реплики: {line_index + 1}\n"
        f"Роль этой реплики: {role}\n"
        f"Ориентир по длине: примерно {target_words} слов (допустимо +/- 50%)\n\n"
        f"Предыдущие реплики:\n{before_ctx}\n\n"
        f"Текущая реплика (заменить):\n{line_index + 1}. {role}: {original_text}\n\n"
        f"Следующие реплики:\n{after_ctx}\n\n"
        f"{instruction_block}"
        f"Контекст документа:\n{doc_context or '(контекст не найден, сохрани исходный смысл реплики)'}\n\n"
        "Сделай реплику естественной, связной с соседними репликами, без повторов соседних фраз. "
        "Не меняй факты и не противоречь следующей реплике."
    )

    try:
        raw = await llm_service.chat_completion(
            system,
            user,
            temperature=0.5,
            max_tokens=700,
        )
    except LMStudioError as e:
        raise HTTPException(503, str(e))

    text = podcast_service._clean_turn_text(str(raw or ""), role)
    if not text:
        raise HTTPException(502, "LM не вернула текст для реплики")
    text = podcast_service._finalize_turn_text(text)
    if tts_friendly:
        text = latin_to_russian_readable_keep_pauses(text)

    next_script = [dict(item) for item in script]
    next_script[line_index] = {**next_script[line_index], "voice": role, "text": text}
    script_plain = _save_script_with_version(
        document_id,
        next_script,
        reason="line_regenerate",
        note=f"line_index={line_index}",
    )
    return {
        "document_id": document_id,
        "line_index": line_index,
        "line": script_plain[line_index],
        "script": script_plain,
        "sources": [
            {
                "chunk_id": r.get("chunk_id"),
                "chunk_index": r.get("chunk_index"),
                "page": r.get("page"),
                "section_path": r.get("section_path"),
                "score": r.get("score"),
            }
            for r in rag_rows[:6]
        ],
    }


@router.get("/podcast_script/{document_id}/timeline")
async def podcast_script_timeline(document_id: str):
    script = _scripts.get(document_id)
    if not script:
        raise HTTPException(404, "Скрипт для документа не найден")
    return script_export_service.estimate_timeline(script)


@router.get("/podcast_script/{document_id}/metrics")
async def podcast_script_metrics(document_id: str):
    script = _scripts.get(document_id)
    if not script:
        raise HTTPException(404, "Скрипт для документа не найден")
    return script_metrics_service.analyse_metrics(script)


@router.get("/quality/{document_id}")
async def get_quality_report(document_id: str):
    doc = document_store.get_document(document_id)
    if not doc:
        raise HTTPException(404, "Документ не найден")

    rag_rows = rag_service.retrieve(document_id, "key ideas and important facts", top_k=8)
    avg_rag_score = round(
        sum(float(r.get("score", 0.0) or 0.0) for r in rag_rows) / max(1, len(rag_rows)),
        4,
    )
    sources = doc.get("sources") or []
    summary = (doc.get("summary") or "").strip()
    script = _scripts.get(document_id) or doc.get("script") or []
    script_metrics = script_metrics_service.analyse_metrics(script) if script else None
    evidence_score = min(1.0, len(sources) / 6.0)
    summary_score = 1.0 if summary else 0.0
    script_score = 1.0 if script else 0.0
    overall = round(
        (avg_rag_score * 0.5) + (evidence_score * 0.25) + (summary_score * 0.15) + (script_score * 0.10),
        3,
    )
    return {
        "document_id": document_id,
        "overall_score": overall,
        "rag": {
            "chunks_found": len(rag_rows),
            "avg_similarity": avg_rag_score,
        },
        "evidence": {
            "sources_count": len(sources),
            "has_summary": bool(summary),
            "has_script": bool(script),
        },
        "script_metrics": script_metrics,
    }


@router.get("/podcast_script/{document_id}/export/txt")
async def podcast_script_export_txt(document_id: str):
    script = _scripts.get(document_id)
    if not script:
        raise HTTPException(404, "Скрипт для документа не найден")
    content = script_export_service.render_txt(script)
    out_path = OUTPUTS_DIR / f"{document_id}_script.txt"
    out_path.write_text(content, encoding="utf-8")
    return FileResponse(path=str(out_path), filename=out_path.name, media_type="text/plain; charset=utf-8")


@router.get("/podcast_script/{document_id}/export/srt")
async def podcast_script_export_srt(document_id: str):
    script = _scripts.get(document_id)
    if not script:
        raise HTTPException(404, "Скрипт для документа не найден")
    content = script_export_service.render_srt(script)
    out_path = OUTPUTS_DIR / f"{document_id}_script.srt"
    out_path.write_text(content, encoding="utf-8")
    return FileResponse(path=str(out_path), filename=out_path.name, media_type="application/x-subrip")


@router.get("/podcast_script/{document_id}/export/docx")
async def podcast_script_export_docx(document_id: str):
    script = _scripts.get(document_id)
    if not script:
        raise HTTPException(404, "Скрипт для документа не найден")
    out_path = OUTPUTS_DIR / f"{document_id}_script.docx"
    script_export_service.save_docx(script, out_path, title=f"Скрипт подкаста {document_id}")
    return FileResponse(
        path=str(out_path),
        filename=out_path.name,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@router.get("/export/{document_id}/report_docx")
async def export_report_docx(document_id: str):
    doc = document_store.get_document(document_id)
    if not doc:
        raise HTTPException(404, "Документ не найден")
    script = _scripts.get(document_id) or doc.get("script") or []
    metrics = script_metrics_service.analyse_metrics(script) if script else None
    out_path = OUTPUTS_DIR / f"{document_id}_report.docx"
    script_export_service.save_report_docx(
        out_path=out_path,
        title=f"Отчёт по документу {document_id}",
        summary=doc.get("summary"),
        sources=doc.get("sources") or [],
        script=script,
        metrics=metrics,
    )
    return FileResponse(
        path=str(out_path),
        filename=out_path.name,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@router.get("/export/{document_id}/bundle")
async def export_document_bundle(document_id: str):
    doc = document_store.get_document(document_id)
    if not doc:
        raise HTTPException(404, "Документ не найден")

    script = _scripts.get(document_id) or doc.get("script") or []
    metrics = script_metrics_service.analyse_metrics(script) if script else None
    timeline = script_export_service.estimate_timeline(script) if script else None
    attachments = list(OUTPUTS_DIR.glob(f"{document_id}_podcast.*"))
    out_path = OUTPUTS_DIR / f"{document_id}_bundle.zip"
    script_export_service.build_document_bundle(
        out_path=out_path,
        document_id=document_id,
        filename=str(doc.get("filename", document_id)),
        summary=doc.get("summary"),
        sources=doc.get("sources") or [],
        script=script,
        metrics=metrics,
        timeline=timeline,
        attachments=attachments,
    )
    return FileResponse(path=str(out_path), filename=out_path.name, media_type="application/zip")


# ---------- E) Podcast audio -----------------------------------------------

@router.post("/podcast_audio/{document_id}")
async def podcast_audio(document_id: str, background_tasks: BackgroundTasks):
    job_id = await _enqueue_audio_job(document_id, background_tasks)
    return {"job_id": job_id}


# ---------- F) Job status ---------------------------------------------------

@router.get("/jobs/{job_id}", response_model=JobInfo)
async def job_status(job_id: str):
    job = await job_manager.get_job_view(job_id)
    if job is None:
        raise HTTPException(404, "Задача не найдена")
    return job


@router.post("/jobs/{job_id}/cancel", response_model=JobInfo)
async def cancel_job(job_id: str):
    job = await job_manager.request_cancel(job_id)
    if job is None:
        raise HTTPException(404, "Задача не найдена")
    refreshed = await job_manager.get_job_view(job_id)
    if refreshed is None:
        raise HTTPException(404, "Задача не найдена")
    return refreshed


@router.get("/jobs/lanes/summary")
async def jobs_lanes():
    return {"lanes": await job_manager.get_lane_stats()}


@router.post("/jobs/{job_id}/retry")
async def retry_job(job_id: str, background_tasks: BackgroundTasks):
    job = await job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Задача не найдена")
    if job.status not in {JobStatus.error, JobStatus.cancelled, JobStatus.done}:
        raise HTTPException(400, "Retry доступен только для завершённых/ошибочных/отменённых задач")
    recipe = job.recipe or {}
    job_type = str(job.job_type or "").strip().lower()
    if job_type == "audio":
        document_id = str(recipe.get("document_id", "")).strip()
        if not document_id:
            raise HTTPException(400, "Для этой задачи недоступен retry (нет document_id)")
        new_job_id = await _enqueue_audio_job(document_id, background_tasks, parent_job_id=job_id)
        await job_manager.update_job(
            job_id,
            status=JobStatus.retrying,
            error=f"Запущен retry: {new_job_id}",
            cancel_requested=False,
        )
        return {"job_id": new_job_id, "parent_job_id": job_id}
    if job_type == "batch":
        if not isinstance(recipe, dict) or not recipe:
            raise HTTPException(400, "Для этой batch-задачи недоступен retry (нет параметров)")
        new_job_id = await _enqueue_batch_job(dict(recipe), background_tasks, parent_job_id=job_id)
        await job_manager.update_job(
            job_id,
            status=JobStatus.retrying,
            error=f"Запущен retry: {new_job_id}",
            cancel_requested=False,
        )
        return {"job_id": new_job_id, "parent_job_id": job_id}
    raise HTTPException(400, "Retry поддерживается только для audio и batch задач")


# ---------- G) Download -----------------------------------------------------

@router.get("/download/{filename}")
async def download(filename: str):
    file_path = OUTPUTS_DIR / filename
    if not file_path.exists():
        raise HTTPException(404, "Файл не найден")
    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type="application/octet-stream",
    )


@router.get("/artifacts/{document_id}")
async def list_artifacts(document_id: str):
    """List generated artifact files for a document."""
    files = [
        p.name
        for p in OUTPUTS_DIR.glob(f"{document_id}*")
        if p.is_file()
    ]
    files.sort()
    return {"document_id": document_id, "files": files}


@router.post("/batch/run")
async def batch_run(body: dict, background_tasks: BackgroundTasks):
    """Batch processing for multiple documents: mode=audio|script_audio."""
    batch_params = _parse_batch_run_params(body)
    job_id = await _enqueue_batch_job(batch_params, background_tasks)
    return {"job_id": job_id}


@router.post("/batch/export")
async def batch_export(body: dict):
    raw_ids = body.get("document_ids")
    if not isinstance(raw_ids, list):
        raise HTTPException(400, "document_ids должен быть массивом")
    document_ids = [str(x).strip() for x in raw_ids if str(x).strip()]
    if not document_ids:
        raise HTTPException(400, "Список document_ids пуст")

    zip_name = f"batch_export_{uuid.uuid4().hex[:8]}.zip"
    zip_path = OUTPUTS_DIR / zip_name
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        meta = {"total": len(document_ids), "items": []}
        for document_id in document_ids:
            doc = document_store.get_document(document_id)
            if not doc:
                meta["items"].append({"document_id": document_id, "status": "missing"})
                continue
            script = _scripts.get(document_id) or doc.get("script") or []
            metrics = script_metrics_service.analyse_metrics(script) if script else None
            timeline = script_export_service.estimate_timeline(script) if script else None
            folder = f"{document_id}/"
            zf.writestr(folder + "document.json", json.dumps(doc, ensure_ascii=False, indent=2))
            if script:
                zf.writestr(folder + "script.txt", script_export_service.render_txt(script))
                zf.writestr(folder + "script.srt", script_export_service.render_srt(script))
            if metrics:
                zf.writestr(folder + "script_metrics.json", json.dumps(metrics, ensure_ascii=False, indent=2))
            if timeline:
                zf.writestr(folder + "timeline.json", json.dumps(timeline, ensure_ascii=False, indent=2))
            out_files = list(OUTPUTS_DIR.glob(f"{document_id}_podcast.*"))
            for p in out_files:
                if p.exists() and p.is_file():
                    zf.write(p, arcname=folder + p.name)
            meta["items"].append({"document_id": document_id, "status": "ok", "media_files": [p.name for p in out_files]})
        zf.writestr("batch_manifest.json", json.dumps(meta, ensure_ascii=False, indent=2))
    return {"filename": zip_name}


# ---------- I) Settings -----------------------------------------------------

@router.get("/settings", response_model=LMStudioSettings)
async def get_settings():
    return LMStudioSettings(**get_lmstudio_settings())


@router.put("/settings", response_model=LMStudioSettings)
async def put_settings(body: LMStudioSettings):
    updated = update_lmstudio_settings(
        base_url=body.base_url,
        model=body.model,
        temperature=body.temperature,
        max_tokens=body.max_tokens,
    )
    return LMStudioSettings(**updated)


@router.get("/settings/voices", response_model=VoiceSettingsResponse)
async def get_voices():
    data = get_voice_settings()
    return VoiceSettingsResponse(
        voices={k: {"model": v["model"], "speaker": v.get("speaker", "0")} for k, v in data["voices"].items()},
        available=data["available"],
    )


@router.get("/settings/role_llm", response_model=RoleLlmSettingsResponse)
async def get_role_llm_settings_api():
    data = get_role_llm_overrides()
    return RoleLlmSettingsResponse(
        role_llm_map={k: {"model": v["model"], "base_url": v.get("base_url")} for k, v in data.items()}
    )


@router.put("/settings/role_llm", response_model=RoleLlmSettingsResponse)
async def put_role_llm_settings_api(body: RoleLlmSettingsResponse):
    try:
        role_map_plain = {
            k: {"model": v.model, **({"base_url": v.base_url} if v.base_url else {})}
            for k, v in (body.role_llm_map or {}).items()
        }
        updated = update_role_llm_overrides(role_map_plain)
        return RoleLlmSettingsResponse(
            role_llm_map={k: {"model": v["model"], "base_url": v.get("base_url")} for k, v in updated.items()}
        )
    except Exception as e:
        raise HTTPException(500, f"Ошибка сохранения LLM по ролям: {e!s}")


@router.put("/settings/voices", response_model=VoiceSettingsResponse)
async def put_voices(body: VoiceSettingsResponse):
    try:
        voices_plain = {k: {"model": v.model, "speaker": v.speaker} for k, v in body.voices.items()}
        updated = update_voice_settings(voices_plain)
        return VoiceSettingsResponse(
            voices={k: {"model": v["model"], "speaker": v.get("speaker", "0")} for k, v in updated["voices"].items()},
            available=updated["available"],
        )
    except Exception as e:
        raise HTTPException(500, f"Ошибка сохранения голосов: {e!s}")


@router.get("/settings/music", response_model=MusicSettings)
async def get_music():
    return MusicSettings(**get_music_settings())


@router.put("/settings/music", response_model=MusicSettings)
async def put_music(body: MusicSettings):
    updated = update_music_settings(body.model_dump())
    return MusicSettings(**updated)


@router.get("/settings/music/files")
async def list_music_files():
    cfg = get_music_settings()
    allowed = {".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac"}
    primary_dir = Path(str(cfg.get("assets_dir", "")))
    fallback_dir = Path("/opt/audio-assets")
    search_dirs: list[Path] = []
    for d in (primary_dir, fallback_dir):
        if d and str(d) and d not in search_dirs and d.exists():
            search_dirs.append(d)
    files_set: set[str] = set()
    for d in search_dirs:
        for p in d.iterdir():
            if p.is_file() and p.suffix.lower() in allowed:
                files_set.add(p.name)
    files = sorted(files_set)
    return {"files": files}


@router.get("/settings/music/file/{filename}")
async def get_music_file(filename: str):
    # path traversal guard
    if Path(filename).name != filename:
        raise HTTPException(400, "Некорректное имя файла")
    cfg = get_music_settings()
    assets_dir = Path(str(cfg.get("assets_dir", "")))
    file_path = assets_dir / filename
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(404, "Музыкальный файл не найден")
    return FileResponse(path=str(file_path), filename=filename, media_type="application/octet-stream")


@router.post("/settings/music/upload")
async def upload_music_file(slot: str = Form(...), file: UploadFile = File(...)):
    slot = str(slot or "").strip().lower()
    slot_map = {"intro": "intro_file", "background": "background_file", "outro": "outro_file"}
    if slot not in slot_map:
        raise HTTPException(400, "slot должен быть одним из: intro, background, outro")
    if not file.filename:
        raise HTTPException(400, "Не выбран файл")
    ext = Path(file.filename).suffix.lower()
    if ext not in {".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac"}:
        raise HTTPException(400, "Поддерживаются только аудио-файлы: mp3, wav, ogg, m4a, aac, flac")

    safe_name = Path(file.filename).name.replace(" ", "_")
    cfg = get_music_settings()
    assets_dir = Path(str(cfg.get("assets_dir", "")))
    assets_dir.mkdir(parents=True, exist_ok=True)
    dst = assets_dir / safe_name
    content = await file.read()
    dst.write_bytes(content)

    updated = update_music_settings({slot_map[slot]: safe_name})
    return {"filename": safe_name, "slot": slot, "settings": updated}


@router.get("/settings/postprocess", response_model=PostprocessSettings)
async def get_postprocess():
    return PostprocessSettings(**get_postprocess_settings())


@router.put("/settings/postprocess", response_model=PostprocessSettings)
async def put_postprocess(body: PostprocessSettings):
    updated = update_postprocess_settings(body.model_dump())
    return PostprocessSettings(**updated)


@router.get("/settings/ocr", response_model=OcrSettings)
async def get_ocr():
    return OcrSettings(**get_ocr_settings())


@router.put("/settings/ocr", response_model=OcrSettings)
async def put_ocr(body: OcrSettings):
    updated = update_ocr_settings(body.model_dump())
    return OcrSettings(**updated)


@router.get("/settings/vision_ingest", response_model=VisionIngestSettings)
async def get_vision_ingest():
    cur = get_vision_ingest_settings()
    return VisionIngestSettings(
        enabled=cur["enabled"],
        base_url=cur["base_url"],
        model=cur["model"] or "",
        timeout_seconds=cur["timeout_seconds"],
        max_images_per_document=cur["max_images_per_document"],
    )


@router.put("/settings/vision_ingest", response_model=VisionIngestSettings)
async def put_vision_ingest(body: VisionIngestSettings):
    updated = update_vision_ingest_settings(body.model_dump())
    return VisionIngestSettings(**updated)


@router.get("/settings/style_profiles")
async def get_style_profiles_api():
    return {"profiles": get_style_profiles()}


@router.get("/settings/scenarios")
async def get_scenarios_api():
    return {"scenarios": podcast_service.get_script_scenarios_catalog()}


@router.put("/settings/scenarios")
async def put_scenario_api(body: dict):
    try:
        scenarios = podcast_service.upsert_script_scenario(body)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"scenarios": scenarios}


@router.delete("/settings/scenarios/{scenario_id}")
async def delete_scenario_api(scenario_id: str):
    try:
        scenarios = podcast_service.delete_script_scenario(scenario_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"scenarios": scenarios}


@router.put("/settings/style_profiles")
async def put_style_profile(body: dict):
    try:
        profiles = upsert_style_profile(body)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"profiles": profiles}


@router.delete("/settings/style_profiles/{profile_id}")
async def remove_style_profile(profile_id: str):
    try:
        profiles = delete_style_profile(profile_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"profiles": profiles}


@router.get("/settings/pronunciation")
async def get_pronunciation():
    return {"overrides": get_pronunciation_overrides()}


@router.put("/settings/pronunciation")
async def put_pronunciation(body: dict):
    overrides = body.get("overrides")
    if not isinstance(overrides, dict):
        raise HTTPException(400, "Ожидается объект overrides: { \"token\": \"замена\" }")
    updated = update_pronunciation_overrides(overrides)
    return {"overrides": updated}


@router.post("/settings/database/clear")
async def clear_database(body: dict):
    """Dangerous operation: wipe all local data artifacts and indexes."""
    if not bool(body.get("confirm_step_1")) or not bool(body.get("confirm_step_2")):
        raise HTTPException(
            400,
            "Требуется двойное подтверждение: confirm_step_1=true и confirm_step_2=true",
        )

    _texts.clear()
    _parsing_document_ids.clear()
    _scripts.clear()
    _script_meta.clear()

    removed_chroma = rag_service.clear_all_indices()
    removed_inputs = _wipe_dir_contents(INPUTS_DIR)
    removed_outputs = _wipe_dir_contents(OUTPUTS_DIR)
    removed_index = _wipe_dir_contents(INDEX_DIR)
    if PARSED_TEXTS_DIR.exists():
        _wipe_dir_contents(PARSED_TEXTS_DIR)

    document_store.clear_all_documents()
    chat_store.clear_all_history()
    await job_manager.clear_all_jobs()

    # Optional generated files in data/ (keep config.yaml and other config files)
    for extra in (DATA_DIR / "style_profiles.json",):
        try:
            if extra.exists():
                extra.unlink()
        except OSError:
            pass

    return {
        "ok": True,
        "removed": {
            "chroma_collections": removed_chroma,
            "inputs_entries": removed_inputs,
            "outputs_entries": removed_outputs,
            "index_entries": removed_index,
        },
    }


VOICE_TEST_TIMEOUT = 120  # секунд на генерацию тестового MP3

@router.post("/settings/voices/test")
async def test_voice(body: dict):
    """Синтез короткого примера для выбранной роли/слота голоса (host/guest1/guest2)."""
    slot = str(body.get("slot", "")).strip()
    if not slot:
        raise HTTPException(400, "Не указан слот голоса (host, guest1, guest2)")

    # Используем текущие настройки голосов и TTS-диспетчер
    script = _normalise_script(
        [{"voice": slot, "text": "Это тест выбранного голоса для подкаста."}]
    )
    if not script:
        raise HTTPException(400, "Не удалось собрать тестовый скрипт для голоса")

    doc_id = f"voice_test_{slot}"
    try:
        mp3_path = await asyncio.wait_for(
            tts_synthesise_script(script, doc_id, progress_cb=None, apply_music=False, apply_postprocess=False),
            timeout=VOICE_TEST_TIMEOUT,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            504,
            f"Тест голоса не успел сгенерироваться за {VOICE_TEST_TIMEOUT} с. Попробуйте ещё раз.",
        )
    return {"filename": mp3_path.name}


@router.get("/settings/test")
async def test_lmstudio_connection(base_url: str | None = None):
    """Quick ping to check if LM Studio is reachable and list available models."""
    import httpx
    from app.config import LMSTUDIO_BASE_URL
    target_base = (base_url or LMSTUDIO_BASE_URL).strip() or LMSTUDIO_BASE_URL
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            resp = await client.get(f"{target_base}/models")
        if resp.status_code == 200:
            data = resp.json()
            models = [m.get("id", "?") for m in data.get("data", [])]
            return {"status": "ok", "models": models, "base_url": target_base}
        return {"status": "error", "detail": f"HTTP {resp.status_code}"}
    except httpx.ConnectError:
        return {"status": "error", "detail": f"Cannot connect to {target_base}"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

"""Persistent project/collection store (JSON in data/)."""

from __future__ import annotations

import json
import threading
from datetime import datetime

from app.config import DATA_DIR

PROJECTS_FILE = DATA_DIR / "projects.json"
_LOCK = threading.Lock()
_DEFAULT_PROJECT_SETTINGS = {
    "chat": {
        "strict_sources": False,
        "use_summary_context": False,
        "question_mode": "default",
        "answer_length": "medium",
        "scope": "auto",
        "knowledge_mode": "document_only",
    },
    "script": {
        "minutes": 5,
        "style": "conversational",
        "scenario": "classic_overview",
        "scenario_options": {},
        "generation_mode": "single_pass",
        "focus": "",
        "tts_friendly": True,
        "knowledge_mode": "document_only",
    },
}


def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _load() -> dict:
    if PROJECTS_FILE.exists():
        try:
            return json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {"projects": {}}
    return {"projects": {}}


def _save(data: dict) -> None:
    PROJECTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROJECTS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(PROJECTS_FILE)


def _normalize_document_ids(document_ids: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in document_ids or []:
        doc_id = str(raw or "").strip()
        if not doc_id or doc_id in seen:
            continue
        seen.add(doc_id)
        out.append(doc_id)
    return out


def _normalize_pinned_qas(items) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        pin_id = str(raw.get("pin_id") or "").strip()
        if not pin_id or pin_id in seen:
            continue
        seen.add(pin_id)
        row = {
            "pin_id": pin_id,
            "question": str(raw.get("question") or "").strip(),
            "answer": str(raw.get("answer") or "").strip(),
            "meta": str(raw.get("meta") or "").strip(),
            "mode": str(raw.get("mode") or "").strip() or None,
            "created_at": str(raw.get("created_at") or "").strip() or _now_iso(),
            "updated_at": str(raw.get("updated_at") or raw.get("created_at") or "").strip() or _now_iso(),
            "citations": raw.get("citations") if isinstance(raw.get("citations"), list) else [],
        }
        if not row["answer"]:
            continue
        out.append(row)
    out.sort(key=lambda x: x.get("updated_at") or x.get("created_at") or "", reverse=True)
    return out


def _normalize_project_settings(settings) -> dict:
    src = settings if isinstance(settings, dict) else {}
    chat_src = src.get("chat") if isinstance(src.get("chat"), dict) else {}
    script_src = src.get("script") if isinstance(src.get("script"), dict) else {}
    scenario_options = script_src.get("scenario_options") if isinstance(script_src.get("scenario_options"), dict) else {}
    minutes_raw = script_src.get("minutes", _DEFAULT_PROJECT_SETTINGS["script"]["minutes"])
    try:
        minutes = int(minutes_raw)
    except (TypeError, ValueError):
        minutes = int(_DEFAULT_PROJECT_SETTINGS["script"]["minutes"])
    question_mode = str(chat_src.get("question_mode", _DEFAULT_PROJECT_SETTINGS["chat"]["question_mode"])).strip().lower()
    if question_mode not in {"default", "quote", "overview", "formulas"}:
        question_mode = "default"
    answer_length = str(chat_src.get("answer_length", _DEFAULT_PROJECT_SETTINGS["chat"]["answer_length"])).strip().lower()
    if answer_length not in {"short", "medium", "long"}:
        answer_length = "medium"
    scope = str(chat_src.get("scope", _DEFAULT_PROJECT_SETTINGS["chat"]["scope"])).strip().lower()
    if scope not in {"auto", "single", "collection"}:
        scope = "auto"
    generation_mode = str(script_src.get("generation_mode", _DEFAULT_PROJECT_SETTINGS["script"]["generation_mode"])).strip().lower()
    if generation_mode not in {"single_pass", "turn_taking"}:
        generation_mode = "single_pass"
    chat_knowledge_mode = str(chat_src.get("knowledge_mode", _DEFAULT_PROJECT_SETTINGS["chat"]["knowledge_mode"])).strip().lower()
    if chat_knowledge_mode not in {"document_only", "hybrid_model"}:
        chat_knowledge_mode = "document_only"
    script_knowledge_mode = str(script_src.get("knowledge_mode", _DEFAULT_PROJECT_SETTINGS["script"]["knowledge_mode"])).strip().lower()
    if script_knowledge_mode not in {"document_only", "hybrid_model"}:
        script_knowledge_mode = "document_only"
    return {
        "chat": {
            "strict_sources": bool(chat_src.get("strict_sources", _DEFAULT_PROJECT_SETTINGS["chat"]["strict_sources"])),
            "use_summary_context": bool(chat_src.get("use_summary_context", _DEFAULT_PROJECT_SETTINGS["chat"]["use_summary_context"])),
            "question_mode": question_mode,
            "answer_length": answer_length,
            "scope": scope,
            "knowledge_mode": chat_knowledge_mode,
        },
        "script": {
            "minutes": max(1, min(60, minutes)),
            "style": str(script_src.get("style", _DEFAULT_PROJECT_SETTINGS["script"]["style"]) or "conversational").strip() or "conversational",
            "scenario": str(script_src.get("scenario", _DEFAULT_PROJECT_SETTINGS["script"]["scenario"]) or "classic_overview").strip() or "classic_overview",
            "scenario_options": scenario_options,
            "generation_mode": generation_mode,
            "focus": str(script_src.get("focus", _DEFAULT_PROJECT_SETTINGS["script"]["focus"]) or ""),
            "tts_friendly": bool(script_src.get("tts_friendly", _DEFAULT_PROJECT_SETTINGS["script"]["tts_friendly"])),
            "knowledge_mode": script_knowledge_mode,
        },
    }


def _normalize_project_row(row: dict | None, *, project_id: str | None = None) -> dict | None:
    if not isinstance(row, dict):
        return None
    pid = str(row.get("project_id") or project_id or "").strip()
    if not pid:
        return None
    name = str(row.get("name") or "").strip()
    if not name:
        return None
    created_at = str(row.get("created_at") or "").strip() or _now_iso()
    updated_at = str(row.get("updated_at") or created_at).strip() or created_at
    return {
        "project_id": pid,
        "name": name,
        "document_ids": _normalize_document_ids(row.get("document_ids") or []),
        "notes": str(row.get("notes") or ""),
        "pinned_qas": _normalize_pinned_qas(row.get("pinned_qas") or []),
        "settings": _normalize_project_settings(row.get("settings")),
        "created_at": created_at,
        "updated_at": updated_at,
    }


def list_projects() -> list[dict]:
    with _LOCK:
        data = _load()
    rows = [
        p for p in (
            _normalize_project_row(row, project_id=pid)
            for pid, row in (data.get("projects") or {}).items()
        )
        if p
    ]
    rows.sort(key=lambda p: (p.get("updated_at") or p.get("created_at") or ""), reverse=True)
    return rows


def get_project(project_id: str) -> dict | None:
    pid = str(project_id or "").strip()
    if not pid:
        return None
    with _LOCK:
        data = _load()
        row = (data.get("projects") or {}).get(pid)
        return _normalize_project_row(row, project_id=pid)


def create_project(project_id: str, name: str, document_ids: list[str] | None = None) -> dict:
    pid = str(project_id or "").strip()
    if not pid:
        raise ValueError("project_id is required")
    title = str(name or "").strip()
    if not title:
        raise ValueError("name is required")
    now = _now_iso()
    row = {
        "project_id": pid,
        "name": title,
        "document_ids": _normalize_document_ids(document_ids),
        "notes": "",
        "pinned_qas": [],
        "settings": _normalize_project_settings(None),
        "created_at": now,
        "updated_at": now,
    }
    with _LOCK:
        data = _load()
        data.setdefault("projects", {})[pid] = row
        _save(data)
    return row


def update_project(
    project_id: str,
    *,
    name: str | None = None,
    document_ids: list[str] | None = None,
    settings: dict | None = None,
) -> dict | None:
    pid = str(project_id or "").strip()
    if not pid:
        return None
    with _LOCK:
        data = _load()
        row = _normalize_project_row((data.get("projects") or {}).get(pid), project_id=pid)
        if not row:
            return None
        if name is not None:
            title = str(name or "").strip()
            if not title:
                raise ValueError("name must not be empty")
            row["name"] = title
        if document_ids is not None:
            row["document_ids"] = _normalize_document_ids(document_ids)
        if settings is not None:
            row["settings"] = _normalize_project_settings(settings)
        row["updated_at"] = _now_iso()
        data.setdefault("projects", {})[pid] = row
        _save(data)
        return _normalize_project_row(row, project_id=pid)


def delete_project(project_id: str) -> bool:
    pid = str(project_id or "").strip()
    if not pid:
        return False
    with _LOCK:
        data = _load()
        projects = data.get("projects") or {}
        if pid not in projects:
            return False
        del projects[pid]
        data["projects"] = projects
        _save(data)
        return True


def remove_document_from_all_projects(document_id: str) -> int:
    did = str(document_id or "").strip()
    if not did:
        return 0
    changed = 0
    with _LOCK:
        data = _load()
        projects = data.get("projects") or {}
        for pid, raw in list(projects.items()):
            row = _normalize_project_row(raw, project_id=pid)
            if not row:
                continue
            ids = _normalize_document_ids(row.get("document_ids") or [])
            if did not in ids:
                projects[pid] = row
                continue
            row["document_ids"] = [x for x in ids if x != did]
            row["updated_at"] = _now_iso()
            projects[pid] = row
            changed += 1
        if changed:
            _save(data)
    return changed


def get_project_notebook(project_id: str) -> dict | None:
    row = get_project(project_id)
    if not row:
        return None
    return {
        "project_id": row.get("project_id"),
        "name": row.get("name"),
        "notes": str(row.get("notes") or ""),
        "pinned_qas": _normalize_pinned_qas(row.get("pinned_qas") or []),
        "updated_at": row.get("updated_at"),
    }


def get_project_settings(project_id: str) -> dict | None:
    row = get_project(project_id)
    if not row:
        return None
    return {
        "project_id": row.get("project_id"),
        "settings": _normalize_project_settings(row.get("settings")),
        "updated_at": row.get("updated_at"),
    }


def set_project_settings(project_id: str, settings: dict) -> dict | None:
    pid = str(project_id or "").strip()
    if not pid:
        return None
    with _LOCK:
        data = _load()
        row = _normalize_project_row((data.get("projects") or {}).get(pid), project_id=pid)
        if not row:
            return None
        row["settings"] = _normalize_project_settings(settings)
        row["updated_at"] = _now_iso()
        data.setdefault("projects", {})[pid] = row
        _save(data)
        return {
            "project_id": pid,
            "settings": row["settings"],
            "updated_at": row["updated_at"],
        }


def set_project_notes(project_id: str, notes: str) -> dict | None:
    pid = str(project_id or "").strip()
    if not pid:
        return None
    with _LOCK:
        data = _load()
        row = _normalize_project_row((data.get("projects") or {}).get(pid), project_id=pid)
        if not row:
            return None
        row["notes"] = str(notes or "")
        row["updated_at"] = _now_iso()
        data.setdefault("projects", {})[pid] = row
        _save(data)
        return {"project_id": pid, "notes": row["notes"], "updated_at": row["updated_at"]}


def add_project_pin(
    project_id: str,
    *,
    pin_id: str,
    question: str = "",
    answer: str,
    citations: list | None = None,
    meta: str = "",
    mode: str | None = None,
) -> dict | None:
    pid = str(project_id or "").strip()
    pin_key = str(pin_id or "").strip()
    if not pid or not pin_key:
        return None
    answer_text = str(answer or "").strip()
    if not answer_text:
        raise ValueError("answer must not be empty")
    now = _now_iso()
    pin = {
        "pin_id": pin_key,
        "question": str(question or "").strip(),
        "answer": answer_text,
        "meta": str(meta or "").strip(),
        "mode": (str(mode or "").strip() or None),
        "citations": citations if isinstance(citations, list) else [],
        "created_at": now,
        "updated_at": now,
    }
    with _LOCK:
        data = _load()
        row = _normalize_project_row((data.get("projects") or {}).get(pid), project_id=pid)
        if not row:
            return None
        pins = _normalize_pinned_qas(row.get("pinned_qas") or [])
        pins = [p for p in pins if str(p.get("pin_id") or "") != pin_key]
        pins.insert(0, pin)
        row["pinned_qas"] = pins[:80]
        row["updated_at"] = now
        data.setdefault("projects", {})[pid] = row
        _save(data)
        return pin


def delete_project_pin(project_id: str, pin_id: str) -> bool:
    pid = str(project_id or "").strip()
    pin_key = str(pin_id or "").strip()
    if not pid or not pin_key:
        return False
    with _LOCK:
        data = _load()
        row = _normalize_project_row((data.get("projects") or {}).get(pid), project_id=pid)
        if not row:
            return False
        pins = _normalize_pinned_qas(row.get("pinned_qas") or [])
        next_pins = [p for p in pins if str(p.get("pin_id") or "") != pin_key]
        if len(next_pins) == len(pins):
            return False
        row["pinned_qas"] = next_pins
        row["updated_at"] = _now_iso()
        data.setdefault("projects", {})[pid] = row
        _save(data)
        return True

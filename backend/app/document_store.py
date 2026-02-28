"""Persistent document store (JSON in data/)."""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path

from app.config import DATA_DIR

logger = logging.getLogger(__name__)

DOCUMENTS_FILE = DATA_DIR / "documents.json"
_LOCK = threading.Lock()


def _load() -> dict:
    """Load documents.json; on error, log and return empty structure without overwriting file."""
    if DOCUMENTS_FILE.exists():
        try:
            with open(DOCUMENTS_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error("Failed to load %s: %s", DOCUMENTS_FILE, e)
    return {"documents": {}}


def _save(data: dict) -> None:
    """Atomically write documents.json to avoid partial files on errors."""
    DOCUMENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = DOCUMENTS_FILE.with_suffix(DOCUMENTS_FILE.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(DOCUMENTS_FILE)


def list_documents() -> list[dict]:
    with _LOCK:
        data = _load()
    docs = list(data.get("documents", {}).values())
    docs.sort(key=lambda d: d.get("created_at", ""), reverse=True)
    return docs


def get_document(document_id: str) -> dict | None:
    with _LOCK:
        data = _load()
        return data.get("documents", {}).get(document_id)


def add_document(document_id: str, filename: str, *, file_hash: str | None = None) -> None:
    with _LOCK:
        data = _load()
        data.setdefault("documents", {})[document_id] = {
            "document_id": document_id,
            "filename": filename,
            "created_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ingested": False,
            "chunks": 0,
            "summary": None,
            "sources": [],
            "script": None,
            "script_meta": {},
            "file_hash": str(file_hash or "").strip() or None,
        }
        _save(data)


def find_document_by_file_hash(file_hash: str) -> dict | None:
    digest = str(file_hash or "").strip().lower()
    if not digest:
        return None
    with _LOCK:
        data = _load()
        for doc in data.get("documents", {}).values():
            if str(doc.get("file_hash") or "").strip().lower() == digest:
                return doc
    return None


def update_document(
    document_id: str,
    *,
    ingested: bool | None = None,
    chunks: int | None = None,
    summary: str | None = None,
    sources: list | None = None,
    script: list | None = None,
    script_meta: dict | None = None,
    file_hash: str | None = None,
) -> None:
    with _LOCK:
        data = _load()
        doc = data.get("documents", {}).get(document_id)
        if not doc:
            return
        if ingested is not None:
            doc["ingested"] = ingested
        if chunks is not None:
            doc["chunks"] = chunks
        if summary is not None:
            doc["summary"] = summary
        if sources is not None:
            doc["sources"] = sources
        if script is not None:
            doc["script"] = script
        if script_meta is not None:
            doc["script_meta"] = script_meta
        if file_hash is not None:
            doc["file_hash"] = str(file_hash or "").strip() or None
        _save(data)


def delete_document(document_id: str) -> None:
    with _LOCK:
        data = _load()
        if document_id in data.get("documents", {}):
            del data["documents"][document_id]
            _save(data)


def clear_all_documents() -> None:
    """Remove all persisted documents from store."""
    with _LOCK:
        _save({"documents": {}})

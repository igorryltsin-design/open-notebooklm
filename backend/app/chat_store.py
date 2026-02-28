"""Persistent chat history store (JSON in data/)."""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

from app.config import DATA_DIR

CHAT_FILE = DATA_DIR / "chat_history.json"
_LOCK = threading.Lock()


def _load() -> dict:
    if CHAT_FILE.exists():
        try:
            return json.loads(CHAT_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {"threads": {}}
    return {"threads": {}}


def _save(data: dict) -> None:
    CHAT_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CHAT_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CHAT_FILE)


def get_history(thread_id: str, limit: int = 60) -> list[dict]:
    tid = str(thread_id or "").strip() or "main-chat"
    with _LOCK:
        data = _load()
        rows = data.get("threads", {}).get(tid, [])
        return rows[-max(1, int(limit)) :]


def append_messages(thread_id: str, messages: list[dict], max_items: int = 200) -> list[dict]:
    tid = str(thread_id or "").strip() or "main-chat"
    with _LOCK:
        data = _load()
        threads = data.setdefault("threads", {})
        rows = threads.setdefault(tid, [])
        stamp = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        for msg in messages:
            role = str(msg.get("role", "assistant") or "assistant")
            text = str(msg.get("text", "") or "").strip()
            if not text:
                continue
            item = {"role": role, "text": text, "created_at": stamp}
            if msg.get("meta"):
                item["meta"] = msg["meta"]
            if msg.get("citations"):
                item["citations"] = msg["citations"]
            rows.append(item)
        threads[tid] = rows[-max(20, int(max_items)) :]
        _save(data)
        return threads[tid]


def clear_history(thread_id: str) -> None:
    tid = str(thread_id or "").strip() or "main-chat"
    with _LOCK:
        data = _load()
        threads = data.get("threads", {})
        if tid in threads:
            del threads[tid]
            _save(data)


def clear_all_history() -> None:
    """Remove all chat threads from persistent store."""
    with _LOCK:
        _save({"threads": {}})

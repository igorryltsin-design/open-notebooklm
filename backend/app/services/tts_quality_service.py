"""TTS text quality checks for podcast scripts."""

from __future__ import annotations

import re
from typing import Any

from app.models import DialogueLine
from app.tts.text_normalize import latin_to_russian_readable_keep_pauses

_LATIN_RE = re.compile(r"[A-Za-z]")
_DIGIT_RE = re.compile(r"\d")
_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_EMAIL_RE = re.compile(r"\b[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_PAUSE_RE = re.compile(r"\[PAUSE_[^\]]+\]", re.IGNORECASE)
_TAG_RE = re.compile(r"[<>{}\[\]]")
_MULTI_SPACE_RE = re.compile(r"\s{2,}")


def _to_line_obj(item: DialogueLine | dict[str, Any]) -> DialogueLine:
    if isinstance(item, DialogueLine):
        return item
    return DialogueLine(**item)


def _issue(code: str, severity: str, message: str) -> dict[str, str]:
    return {"code": code, "severity": severity, "message": message}


def analyse_script(script: list[DialogueLine] | list[dict[str, Any]]) -> dict[str, Any]:
    """Return line-level TTS quality findings for the given script."""
    rows: list[dict[str, Any]] = []
    errors = 0
    warns = 0

    for idx, raw in enumerate(script):
        line = _to_line_obj(raw)
        text = (line.text or "").strip()
        issues: list[dict[str, str]] = []

        if not text:
            issues.append(_issue("empty_text", "error", "Пустой текст реплики."))
        if _LATIN_RE.search(text):
            issues.append(_issue("latin_chars", "error", "Есть латиница: TTS может читать плохо."))
        if _DIGIT_RE.search(text):
            issues.append(_issue("digits", "error", "Есть цифры: лучше писать числа словами."))
        if _URL_RE.search(text):
            issues.append(_issue("url", "warn", "Есть URL: лучше заменить на читаемую фразу."))
        if _EMAIL_RE.search(text):
            issues.append(_issue("email", "warn", "Есть email: лучше заменить на читаемую форму."))
        if _PAUSE_RE.search(text):
            issues.append(_issue("pause_marker", "warn", "Найден маркер [PAUSE_*]: будет удалён нормализатором."))
        if _TAG_RE.search(text):
            issues.append(_issue("special_tags", "warn", "Есть спецсимволы/теги, возможна неестественная озвучка."))
        if _MULTI_SPACE_RE.search(text):
            issues.append(_issue("spacing", "warn", "Лишние пробелы в тексте."))
        if len(text) > 260:
            issues.append(_issue("long_line", "warn", "Слишком длинная реплика; лучше разбить на две."))

        suggestion = latin_to_russian_readable_keep_pauses(text) if text else text
        if suggestion and suggestion != text:
            issues.append(_issue("normalizable", "warn", "Текст можно улучшить автоматической нормализацией."))

        for it in issues:
            if it["severity"] == "error":
                errors += 1
            else:
                warns += 1

        rows.append(
            {
                "index": idx,
                "voice": line.voice,
                "issues": issues,
                "suggestion": suggestion,
            }
        )

    return {
        "lines": rows,
        "totals": {"errors": errors, "warnings": warns, "lines": len(rows)},
    }


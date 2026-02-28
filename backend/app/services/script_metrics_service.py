"""Script quality metrics for podcast text."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from app.models import DialogueLine
from app.services import script_export_service

_WORD_RE = re.compile(r"[А-Яа-яA-Za-zЁё0-9\-]+")
_SENT_SPLIT_RE = re.compile(r"[.!?]+")
_RU_STOP = {
    "и", "в", "на", "с", "по", "к", "о", "об", "у", "за", "из", "от", "до",
    "что", "это", "как", "но", "а", "или", "не", "да", "ли", "мы", "вы",
}


def _to_line_obj(item: DialogueLine | dict[str, Any]) -> DialogueLine:
    if isinstance(item, DialogueLine):
        return item
    return DialogueLine(**item)


def analyse_metrics(script: list[DialogueLine] | list[dict[str, Any]]) -> dict[str, Any]:
    lines: list[DialogueLine] = [_to_line_obj(x) for x in script]
    if not lines:
        return {
            "totals": {},
            "quality": {},
            "top_terms": [],
            "by_voice": {},
        }

    texts = [line.text or "" for line in lines]
    full_text = " ".join(texts)
    words = _WORD_RE.findall(full_text)
    words_l = [w.lower() for w in words]
    total_words = len(words_l)
    unique_words = len(set(words_l))
    lexical_diversity = round(unique_words / total_words, 3) if total_words else 0.0

    repeats = Counter(words_l)
    repeated_share = round(
        sum(v for _, v in repeats.items() if v > 1) / total_words, 3
    ) if total_words else 0.0

    sentence_chunks = [s.strip() for s in _SENT_SPLIT_RE.split(full_text) if s.strip()]
    avg_sentence_words = round(
        sum(len(_WORD_RE.findall(s)) for s in sentence_chunks) / max(1, len(sentence_chunks)),
        2,
    )
    avg_line_words = round(total_words / len(lines), 2)

    timeline = script_export_service.estimate_timeline(lines)
    total_sec = float(timeline.get("total_duration_sec", 0.0)) or 1.0
    wpm = round(total_words / (total_sec / 60.0), 1)

    content_words = [w for w in words_l if w not in _RU_STOP and len(w) >= 5]
    top_terms = [
        {"term": term, "count": count}
        for term, count in Counter(content_words).most_common(12)
    ]
    long_word_share = round(sum(1 for w in words_l if len(w) >= 10) / max(1, total_words), 3)

    by_voice: dict[str, dict[str, float | int]] = {}
    for line in lines:
        voice = line.voice
        by_voice.setdefault(voice, {"lines": 0, "words": 0})
        by_voice[voice]["lines"] += 1
        by_voice[voice]["words"] += len(_WORD_RE.findall(line.text or ""))
    for voice, stats in by_voice.items():
        stats["avg_words_per_line"] = round(stats["words"] / max(1, stats["lines"]), 2)

    quality = {
        "speech_rate_wpm": wpm,
        "speech_rate_ok": 120 <= wpm <= 170,
        "lexical_diversity": lexical_diversity,
        "repeated_share": repeated_share,
        "avg_sentence_words": avg_sentence_words,
        "avg_line_words": avg_line_words,
        "long_word_share": long_word_share,
    }

    totals = {
        "lines": len(lines),
        "words": total_words,
        "unique_words": unique_words,
        "chapters": len(timeline.get("chapters", [])),
        "duration_sec_estimate": round(total_sec, 2),
    }

    return {
        "totals": totals,
        "quality": quality,
        "top_terms": top_terms,
        "by_voice": by_voice,
    }


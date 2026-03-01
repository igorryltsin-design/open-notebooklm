"""Generate summary and podcast scripts via LLM + RAG."""

from __future__ import annotations

import json
import logging
import re
import uuid
import hashlib
from statistics import mean
from typing import Any, AsyncGenerator

from app.config import DATA_DIR, get_style_profiles
from app.models import DialogueLine, SourceFragment
from app import document_store
from app.services import llm_service, rag_service
from app.tts.text_normalize import latin_to_russian_readable_keep_pauses

logger = logging.getLogger(__name__)

SCRIPT_SCENARIOS_FILE = DATA_DIR / "script_scenarios.json"
TTS_REWRITE_TIMEOUT_SECONDS = 20 * 60

QUESTION_MODES: dict[str, dict[str, Any]] = {
    "default": {
        "label": "Стандартный",
        "instruction": "Сформулируй короткий, точный ответ по фрагментам.",
        "max_chunks": 8,
        "fallback_chunks": 3,
        "require_terms": False,
        "prefer_math_chunks": False,
    },
    "quote": {
        "label": "Цитата",
        "instruction": (
            "Дай ответ с 1-3 короткими дословными цитатами из контекста. "
            "После каждой цитаты укажи [doc/chunk]."
        ),
        "max_chunks": 10,
        "fallback_chunks": 4,
        "require_terms": True,
        "prefer_math_chunks": False,
    },
    "overview": {
        "label": "Структурный обзор",
        "instruction": "Дай структурированный обзор: тезисы, ключевые термины, вывод.",
        "max_chunks": 12,
        "fallback_chunks": 5,
        "require_terms": False,
        "prefer_math_chunks": False,
    },
    "formulas": {
        "label": "Формулы и графики",
        "instruction": (
            "Сфокусируйся на формулах, числах, графиках и технических обозначениях. "
            "Если таких данных нет, явно сообщи об этом."
        ),
        "max_chunks": 10,
        "fallback_chunks": 4,
        "require_terms": False,
        "prefer_math_chunks": True,
    },
}

ANSWER_LENGTHS: dict[str, dict[str, Any]] = {
    "short": {
        "label": "Короткий",
        "instruction": "Сделай ответ кратким: 2-4 предложения или 3-5 пунктов.",
        "max_tokens": 900,
    },
    "medium": {
        "label": "Средний",
        "instruction": "Сделай ответ средней длины: компактно, но с ключевыми деталями.",
        "max_tokens": 2200,
    },
    "long": {
        "label": "Длинный",
        "instruction": "Сделай развернутый ответ: структура, детали, ограничения и вывод.",
        "max_tokens": 3600,
    },
}


def _style_instruction(style_id: str) -> tuple[str, str]:
    profiles = get_style_profiles()
    for p in profiles:
        if str(p.get("id", "")).strip().lower() == style_id.strip().lower():
            return str(p.get("name", style_id)), str(p.get("instruction", ""))
    return style_id, ""


def _document_title_for_query(document_id: str) -> str:
    try:
        doc = document_store.get_document(document_id)
    except Exception:
        doc = None
    return str((doc or {}).get("filename") or "").strip()


def _build_podcast_retrieval_query(
    document_id: str,
    *,
    scenario_label: str,
    style_name: str,
    focus: str | None = None,
) -> str:
    title = _document_title_for_query(document_id)
    focus_text = str(focus or "").strip()
    parts = [
        "podcast script discussion topics key ideas facts examples definitions practical points",
        f"scenario {scenario_label}",
        f"style {style_name}",
    ]
    if title:
        parts.append(f"document title {title}")
    if focus_text:
        parts.append(f"user focus {focus_text}")
    # Russian mirror to improve multilingual retrieval on RU documents.
    ru_parts = ["подкаст разбор документа ключевые идеи факты примеры определения практические моменты"]
    if title:
        ru_parts.append(f"название документа: {title}")
    ru_parts.append(f"сценарий: {scenario_label}")
    ru_parts.append(f"стиль: {style_name}")
    if focus_text:
        ru_parts.append(f"фокус выпуска: {focus_text}")
    return " | ".join(parts + ru_parts)


def _resolve_question_mode(mode: str | None) -> tuple[str, dict[str, Any]]:
    key = str(mode or "default").strip().lower()
    if key not in QUESTION_MODES:
        key = "default"
    return key, QUESTION_MODES[key]


def resolve_answer_length(answer_length: str | None) -> tuple[str, dict[str, Any]]:
    key = str(answer_length or "medium").strip().lower()
    if key not in ANSWER_LENGTHS:
        key = "medium"
    return key, ANSWER_LENGTHS[key]


_EXTERNAL_KNOWLEDGE_RE = re.compile(r"(^|\n)\s*(?:Вне документа|Гипотеза модели|Предложение модели|Критика модели)\s*:", flags=re.IGNORECASE)
_HYBRID_SCRIPT_SCENARIOS = {"debate", "critique"}


def _normalize_knowledge_mode(mode: str | None) -> str:
    key = str(mode or "document_only").strip().lower()
    if key not in {"document_only", "hybrid_model"}:
        key = "document_only"
    return key


def _effective_script_knowledge_mode(scenario: str | None, knowledge_mode: str | None) -> str:
    mode = _normalize_knowledge_mode(knowledge_mode)
    scenario_key, _ = _resolve_script_scenario(scenario)
    if mode == "hybrid_model" and scenario_key in _HYBRID_SCRIPT_SCENARIOS:
        return mode
    return "document_only"


def _answer_has_model_knowledge_content(answer: str) -> bool:
    return bool(_EXTERNAL_KNOWLEDGE_RE.search(str(answer or "")))


def _line_grounding(text: str, *, effective_knowledge_mode: str) -> str:
    if effective_knowledge_mode == "hybrid_model" and _answer_has_model_knowledge_content(text):
        return "hybrid_external"
    return "document"


def _extract_terms(text: str) -> list[str]:
    terms = [t for t in re.findall(r"\w+", (text or "").lower()) if len(t) >= 3]
    seen: set[str] = set()
    uniq: list[str] = []
    for t in terms:
        if t in seen:
            continue
        seen.add(t)
        uniq.append(t)
    return uniq


def _snippet_has_math(text: str) -> bool:
    s = (text or "").lower()
    if re.search(r"[=+\-*/^<>]", s):
        return True
    return bool(re.search(r"\b(sql|json|api|llm|nl2sql|din-sql|resdsql|\d+[.,]?\d*)\b", s))


def _extract_highlights(text: str, question_terms: list[str], limit: int = 3) -> list[str]:
    lines = [ln.strip() for ln in re.split(r"[\n\r]+", text or "") if ln.strip()]
    if not lines:
        return []
    boosted: list[tuple[int, str]] = []
    for line in lines:
        low = line.lower()
        score = 0
        for term in question_terms:
            if term in low:
                score += 1
        if _snippet_has_math(line):
            score += 1
        boosted.append((score, line))
    boosted.sort(key=lambda x: x[0], reverse=True)
    out: list[str] = []
    for score, line in boosted:
        if score <= 0 and out:
            continue
        out.append(line[:220] + ("..." if len(line) > 220 else ""))
        if len(out) >= limit:
            break
    if not out:
        out.append(lines[0][:220] + ("..." if len(lines[0]) > 220 else ""))
    return out


# ---------- Summary --------------------------------------------------------

def _summary_prompts(document_id: str) -> tuple[str, str, list]:
    """Build (system, user, chunks) for summary. Raises ValueError if no chunks."""
    chunks = rag_service.retrieve(document_id, "main topics and key ideas", top_k=10)
    if not chunks:
        raise ValueError(f"No indexed content for document {document_id}. Run ingest first.")
    context = "\n\n---\n\n".join(c["text"] for c in chunks)
    system = (
        "You are a helpful research assistant. "
        "Answer only in Russian. "
        "Summarise the following material in 3-5 concise paragraphs in Russian. "
        "After the summary, list short source citations as bullet points in Russian, "
        "each quoting a short fragment (1-2 sentences) from the source text. "
        "Keep terminology from the source if it was in another language, but the rest must be in Russian."
    )
    user = f"Material:\n\n{context}"
    return system, user, chunks


def clean_summary_output(raw: str) -> str:
    """Remove common instruction-echo preambles from summary output."""
    text = str(raw or "").strip()
    if not text:
        return text
    text = re.sub(r"^```(?:json|markdown|md|text)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)

    meta_re = re.compile(
        r"^(?:вот|ниже|предлагаю|представляю)?\s*"
        r"(?:кратк(?:ое|ий)\s+(?:изложение|саммари|обзор)|саммари|обзор)\b"
        r"[\s\S]{0,220}?(?:параграф|абзац|цитат|материал)",
        flags=re.IGNORECASE,
    )
    heading_re = re.compile(
        r"^(?:кратк(?:ое|ий)\s+(?:изложение|саммари|обзор)|саммари|обзор)\s*[:\-]\s*$",
        flags=re.IGNORECASE,
    )

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        return text

    first = paragraphs[0]
    # If the first paragraph is only a meta introduction, drop it.
    if meta_re.search(first):
        if len(paragraphs) > 1:
            paragraphs = paragraphs[1:]
        else:
            # Try to trim the meta sentence and keep the remainder.
            split = re.split(r"(?<=[\.\!\?])\s+", first, maxsplit=1)
            if len(split) == 2 and meta_re.search(split[0]):
                paragraphs[0] = split[1].strip()
            else:
                colon_split = first.split(":", 1)
                if len(colon_split) == 2 and meta_re.search(colon_split[0]):
                    paragraphs[0] = colon_split[1].strip()
    elif heading_re.match(first) and len(paragraphs) > 1:
        paragraphs = paragraphs[1:]

    cleaned = "\n\n".join(p for p in paragraphs if p.strip()).strip()
    return cleaned or text


async def generate_summary(document_id: str) -> tuple[str, list[SourceFragment]]:
    """Produce a concise summary using retrieved chunks."""
    system, user, chunks = _summary_prompts(document_id)
    raw = await llm_service.chat_completion(system, user, temperature=0.3)
    summary = clean_summary_output(raw)
    sources: list[SourceFragment] = []
    for i, c in enumerate(chunks[:6]):
        sources.append(SourceFragment(
            chunk_id=c["chunk_id"],
            text=c["text"][:200] + ("..." if len(c["text"]) > 200 else ""),
        ))
    return summary, sources


# ---------- Podcast script -------------------------------------------------

SCRIPT_SCENARIOS: dict[str, dict[str, Any]] = {
    "classic_overview": {
        "label": "Классический обзор",
        "min_roles": 1,
        "max_roles": 8,
        "prompt_ru": (
            "Структура: ведущий и гости разбирают документ, последовательно раскрывая ключевые идеи, "
            "факты и выводы. Реплики должны развивать одну тему, а не быть набором несвязанных тезисов."
        ),
        "prompt_en": (
            "Structure: a host and guests discuss the document, unpacking key ideas, facts, and conclusions "
            "in a coherent progression."
        ),
    },
    "interview": {
        "label": "Интервью",
        "min_roles": 2,
        "max_roles": 6,
        "prompt_ru": (
            "Структура: интервью. Ведущий задаёт вопросы по материалу, гость(и) отвечают по содержанию документа. "
            "Вопросы должны быть конкретными, ответы — опираться на текст."
        ),
        "prompt_en": (
            "Structure: interview. The host asks focused questions about the material and the guest(s) answer "
            "using information from the document."
        ),
    },
    "debate": {
        "label": "Дебаты",
        "min_roles": 2,
        "max_roles": 6,
        "prompt_ru": (
            "Структура: спор/дебаты. Спикеры занимают разные позиции и приводят аргументы/контраргументы по документу. "
            "Важно явно противопоставлять позиции и завершить коротким резюме."
        ),
        "prompt_en": (
            "Structure: debate. Speakers take different positions and exchange arguments/counterarguments grounded in the document, "
            "then end with a short summary."
        ),
    },
    "critique": {
        "label": "Критика и улучшения",
        "min_roles": 2,
        "max_roles": 6,
        "prompt_ru": (
            "Структура: сначала коротко и точно изложите, что говорит документ, затем перейдите к критике, слабым местам, "
            "рискам и улучшениям. В финале дайте практические рекомендации."
        ),
        "prompt_en": (
            "Structure: first summarize what the document says, then move to critique, gaps, risks, and improvements. "
            "Finish with practical recommendations."
        ),
    },
    "round_table": {
        "label": "Круглый стол",
        "min_roles": 3,
        "max_roles": 8,
        "prompt_ru": (
            "Структура: круглый стол. Несколько ролей с разным взглядом (например, практик, теоретик, скептик, модератор). "
            "Каждый спикер должен добавлять уникальный угол зрения."
        ),
        "prompt_en": (
            "Structure: round table. Multiple roles contribute distinct perspectives (e.g., practitioner, theorist, skeptic, moderator)."
        ),
    },
    "educational": {
        "label": "Образовательный",
        "min_roles": 2,
        "max_roles": 4,
        "prompt_ru": (
            "Структура: учитель объясняет материал по документу, ученик задаёт короткие уточняющие вопросы. "
            "Объяснения должны быть ясными, с примерами и короткими выводами."
        ),
        "prompt_en": (
            "Structure: educational. A teacher explains the document and a student asks short clarifying questions."
        ),
    },
    "news_digest": {
        "label": "Новостной дайджест",
        "min_roles": 1,
        "max_roles": 4,
        "prompt_ru": (
            "Структура: новостной дайджест. Короткие блоки 'главное', затем детали и краткий вывод по каждому блоку."
        ),
        "prompt_en": (
            "Structure: news digest. Short 'top stories' blocks, details, and a brief takeaway per block."
        ),
    },
    "investigation": {
        "label": "Расследование",
        "min_roles": 1,
        "max_roles": 5,
        "prompt_ru": (
            "Структура: расследование. Ведущий формулирует гипотезы, затем проверяет их по тексту документа, "
            "и завершает выводами."
        ),
        "prompt_en": (
            "Structure: investigation. Form hypotheses, verify them against the document text, and conclude."
        ),
    },
}

_SCENARIO_DEFAULT_ROLES: dict[str, list[str]] = {
    "classic_overview": ["host", "guest1", "guest2"],
    "interview": ["host", "guest"],
    "debate": ["moderator", "speaker_a", "speaker_b"],
    "critique": ["moderator", "critic", "builder"],
    "round_table": ["moderator", "practitioner", "theorist", "skeptic"],
    "educational": ["teacher", "student"],
    "news_digest": ["host"],
    "investigation": ["host", "skeptic"],
}

_SCENARIO_SUPPORTED_OPTIONS: dict[str, list[dict[str, Any]]] = {
    "debate": [
        {"id": "stance_a", "type": "string", "label": "Позиция A", "default": "скептик"},
        {"id": "stance_b", "type": "string", "label": "Позиция B", "default": "оптимист"},
    ],
    "news_digest": [
        {"id": "block_count", "type": "int", "label": "Количество блоков", "min": 2, "max": 12, "default": 4},
        {"id": "tone", "type": "string", "label": "Тон", "default": "нейтральный"},
    ],
    "educational": [
        {"id": "student_question_frequency", "type": "string", "label": "Частота вопросов ученика", "default": "medium"},
    ],
    "investigation": [
        {"id": "steps", "type": "string_list", "label": "Шаги расследования", "default": ["гипотеза", "проверка", "вывод"]},
    ],
}


def _script_scenarios_file_read() -> list[dict[str, Any]]:
    if not SCRIPT_SCENARIOS_FILE.exists():
        return []
    try:
        payload = json.loads(SCRIPT_SCENARIOS_FILE.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)]
    except Exception as e:
        logger.warning("Failed to read custom script scenarios from %s: %s", SCRIPT_SCENARIOS_FILE, e)
    return []


def _script_scenarios_file_write(items: list[dict[str, Any]]) -> None:
    SCRIPT_SCENARIOS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SCRIPT_SCENARIOS_FILE.write_text(
        json.dumps(items, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _slugify_scenario_id(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", str(name or "").strip().lower()).strip("-")
    if not base:
        base = "scenario"
    return base


def _sanitize_custom_script_scenario(raw: dict[str, Any]) -> dict[str, Any]:
    scenario_id = str(raw.get("id", "")).strip().lower()
    label = str(raw.get("name") or raw.get("label") or scenario_id).strip()
    prompt_ru = str(raw.get("description") or raw.get("prompt_ru") or "").strip()
    prompt_en = str(raw.get("prompt_en") or "").strip() or prompt_ru
    min_roles = _safe_int(raw.get("min_roles"), default=1, min_value=1, max_value=20)
    max_roles = _safe_int(raw.get("max_roles"), default=max(min_roles, 8), min_value=min_roles, max_value=20)
    supported_options = raw.get("supported_options")
    if not isinstance(supported_options, list):
        supported_options = []
    return {
        "id": scenario_id,
        "label": label or scenario_id,
        "prompt_ru": prompt_ru,
        "prompt_en": prompt_en,
        "min_roles": min_roles,
        "max_roles": max_roles,
        "supported_options": supported_options,
        "is_builtin": False,
    }


def _combined_script_scenarios() -> dict[str, dict[str, Any]]:
    combined: dict[str, dict[str, Any]] = {
        sid: {**meta, "is_builtin": True}
        for sid, meta in SCRIPT_SCENARIOS.items()
    }
    for raw in _script_scenarios_file_read():
        sanitized = _sanitize_custom_script_scenario(raw)
        sid = str(sanitized.get("id", "")).strip().lower()
        if not sid:
            continue
        combined[sid] = {
            "label": sanitized.get("label", sid),
            "prompt_ru": sanitized.get("prompt_ru", ""),
            "prompt_en": sanitized.get("prompt_en", ""),
            "min_roles": int(sanitized.get("min_roles", 1) or 1),
            "max_roles": int(sanitized.get("max_roles", 8) or 8),
            "supported_options": list(sanitized.get("supported_options", [])),
            "is_builtin": False,
        }
    return combined


def upsert_script_scenario(profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Create or update a custom script scenario in data/script_scenarios.json."""
    if not isinstance(profile, dict):
        raise ValueError("Ожидается объект сценария")
    raw_id = str(profile.get("id", "")).strip().lower()
    name = str(profile.get("name") or profile.get("label") or "").strip()
    description = str(profile.get("description") or profile.get("prompt_ru") or "").strip()
    if not name:
        raise ValueError("name обязателен")
    if not description:
        raise ValueError("description (или prompt_ru) обязателен")
    if not raw_id:
        raw_id = f"{_slugify_scenario_id(name)}-{uuid.uuid4().hex[:4]}"
    if raw_id in SCRIPT_SCENARIOS:
        raise ValueError("Нельзя перезаписать встроенный сценарий. Используйте другое id.")

    custom_items = _script_scenarios_file_read()
    next_item = {
        "id": raw_id,
        "name": name,
        "description": description,
        "prompt_en": str(profile.get("prompt_en") or "").strip(),
        "min_roles": _safe_int(profile.get("min_roles"), default=1, min_value=1, max_value=20),
        "max_roles": _safe_int(profile.get("max_roles"), default=8, min_value=1, max_value=20),
        "supported_options": profile.get("supported_options") if isinstance(profile.get("supported_options"), list) else [],
    }
    if next_item["max_roles"] < next_item["min_roles"]:
        next_item["max_roles"] = next_item["min_roles"]

    replaced = False
    out: list[dict[str, Any]] = []
    for item in custom_items:
        if str(item.get("id", "")).strip().lower() == raw_id:
            out.append(next_item)
            replaced = True
        else:
            out.append(item)
    if not replaced:
        out.append(next_item)
    _script_scenarios_file_write(out)
    return get_script_scenarios_catalog()


def delete_script_scenario(scenario_id: str) -> list[dict[str, Any]]:
    """Delete custom script scenario by id."""
    sid = str(scenario_id or "").strip().lower()
    if not sid:
        raise ValueError("scenario_id обязателен")
    if sid in SCRIPT_SCENARIOS:
        raise ValueError("Нельзя удалить встроенный сценарий")
    custom_items = _script_scenarios_file_read()
    next_items = [x for x in custom_items if str(x.get("id", "")).strip().lower() != sid]
    if len(next_items) == len(custom_items):
        raise ValueError("Сценарий не найден")
    _script_scenarios_file_write(next_items)
    return get_script_scenarios_catalog()


def get_script_scenarios_catalog() -> list[dict[str, Any]]:
    """Metadata catalog for UI scenario picker."""
    out: list[dict[str, Any]] = []
    combined = _combined_script_scenarios()
    for scenario_id, meta in combined.items():
        out.append({
            "id": scenario_id,
            "name": str(meta.get("label", scenario_id)),
            "description": str(meta.get("prompt_ru", "")),
            "min_roles": int(meta.get("min_roles", 1) or 1),
            "max_roles": int(meta.get("max_roles", 99) or 99),
            "default_roles": list(_SCENARIO_DEFAULT_ROLES.get(scenario_id, ["host", "guest1", "guest2"])),
            "supported_options": list(meta.get("supported_options", _SCENARIO_SUPPORTED_OPTIONS.get(scenario_id, []))),
            "is_builtin": bool(meta.get("is_builtin", scenario_id in SCRIPT_SCENARIOS)),
        })
    # Built-ins first, then customs by name.
    out.sort(key=lambda x: (0 if x.get("is_builtin") else 1, str(x.get("name", "")).lower()))
    return out


def _safe_int(value: Any, default: int, min_value: int | None = None, max_value: int | None = None) -> int:
    try:
        out = int(value)
    except Exception:
        out = default
    if min_value is not None:
        out = max(min_value, out)
    if max_value is not None:
        out = min(max_value, out)
    return out


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    s = str(value).strip().lower()
    if s in {"1", "true", "yes", "y", "on", "да"}:
        return True
    if s in {"0", "false", "no", "n", "off", "нет"}:
        return False
    return default


def _is_context_overflow_error(exc: Exception) -> bool:
    s = str(exc or "").lower()
    return (
        "context length" in s
        or "n_keep" in s
        or ("tokens to keep" in s and "initial prompt" in s)
        or "prompt is too long" in s
    )


def _compose_script_context(
    chunks: list[dict[str, Any]],
    *,
    max_chars: int,
    max_chunks: int,
    per_chunk_chars: int = 1200,
) -> str:
    """Build bounded document context text for LLM prompts."""
    if not chunks:
        return ""
    total_cap = max(400, int(max_chars))
    chunk_cap = max(120, int(per_chunk_chars))
    use_chunks = chunks[: max(1, int(max_chunks))]
    parts: list[str] = []
    used = 0
    for row in use_chunks:
        text = _truncate_prompt_text(str((row or {}).get("text") or ""), chunk_cap).strip()
        if not text:
            continue
        sep = "\n\n---\n\n" if parts else ""
        need = len(sep) + len(text)
        if parts and used + need > total_cap:
            break
        if not parts and need > total_cap:
            text = _truncate_prompt_text(text, max(120, total_cap - 1))
            need = len(text)
        parts.append((sep + text) if sep else text)
        used += need
        if used >= total_cap:
            break
    joined = "".join(parts).strip()
    return _truncate_prompt_text(joined, total_cap)


def _resolve_script_scenario(scenario_id: str | None) -> tuple[str, dict[str, Any]]:
    key = str(scenario_id or "classic_overview").strip().lower()
    combined = _combined_script_scenarios()
    if key not in combined:
        key = "classic_overview"
    return key, combined[key]


def _validate_scenario_roles(scenario_id: str, voice_list: list[str]) -> None:
    _, meta = _resolve_script_scenario(scenario_id)
    n = len(voice_list)
    min_roles = int(meta.get("min_roles", 1) or 1)
    max_roles = int(meta.get("max_roles", 99) or 99)
    if n < min_roles:
        raise ValueError(
            f"Сценарий '{scenario_id}' требует минимум {min_roles} ролей, передано {n}."
        )
    if n > max_roles:
        raise ValueError(
            f"Сценарий '{scenario_id}' поддерживает максимум {max_roles} ролей, передано {n}."
        )


def _scenario_extra_guidance(
    scenario_id: str,
    scenario_options: dict[str, Any] | None,
    voice_list: list[str],
) -> tuple[str, str]:
    opts = scenario_options or {}
    key, _ = _resolve_script_scenario(scenario_id)

    if key == "debate":
        speaker_a = voice_list[1] if len(voice_list) > 1 else voice_list[0]
        speaker_b = voice_list[2] if len(voice_list) > 2 else (voice_list[1] if len(voice_list) > 1 else voice_list[0])
        stance_a = str(opts.get("stance_a") or "скептик").strip()
        stance_b = str(opts.get("stance_b") or "оптимист").strip()
        ru = (
            f"Назначение ролей для дебатов: {speaker_a} — позиция '{stance_a}', "
            f"{speaker_b} — позиция '{stance_b}'. "
            "Если есть ведущий/модератор, он направляет дискуссию и подводит итог."
        )
        en = (
            f"Debate role assignment: {speaker_a} takes the '{stance_a}' stance, "
            f"{speaker_b} takes the '{stance_b}' stance. "
            "If a host/moderator is present, they guide the discussion and summarize."
        )
        return ru, en

    if key == "critique":
        ru = (
            "Сначала коротко изложите позицию документа, затем явно разделяйте подтвержденные тезисы и внешнюю критику. "
            "Внешние замечания, альтернативы и улучшения помечайте префиксом 'Вне документа:' или 'Критика модели:'."
        )
        en = (
            "First summarize the document's position, then clearly separate document-backed claims from external critique. "
            "Prefix external critique, alternatives, and improvements with 'Вне документа:' or 'Критика модели:'."
        )
        return ru, en

    if key == "news_digest":
        block_count = _safe_int(opts.get("block_count"), default=4, min_value=2, max_value=12)
        tone = str(opts.get("tone") or "нейтральный").strip()
        ru = (
            f"Сделай {block_count} коротких блока(ов) 'главное'. Тон подачи: {tone}. "
            "Предпочтительно использовать первого спикера как основного ведущего."
        )
        en = (
            f"Produce {block_count} short 'main point' blocks. Presentation tone: {tone}. "
            "Prefer the first speaker as the primary anchor."
        )
        return ru, en

    if key == "educational":
        freq = str(opts.get("student_question_frequency") or "medium").strip()
        ru = (
            f"Частота вопросов ученика: {freq}. Ученик задаёт короткие вопросы, "
            "учитель отвечает понятнее и глубже."
        )
        en = (
            f"Student question frequency: {freq}. The student asks short questions and the teacher responds with clear explanations."
        )
        return ru, en

    if key == "investigation":
        steps = opts.get("steps")
        if isinstance(steps, list):
            steps_list = [str(s).strip() for s in steps if str(s).strip()]
        else:
            steps_list = []
        if not steps_list:
            steps_list = ["гипотеза", "проверка", "вывод"]
        ru = "Следуй шагам расследования: " + " → ".join(steps_list) + "."
        en = "Follow investigation steps: " + " -> ".join(steps_list) + "."
        return ru, en

    if key == "round_table":
        ru = (
            "Старайся равномерно распределять реплики между ролями и избегать длинных монологов одного спикера подряд."
        )
        en = "Try to distribute speaking turns evenly and avoid long uninterrupted monologues by one role."
        return ru, en

    return "", ""


def _resolve_primary_role_llm_override(
    voices: list[str],
    role_llm_map: dict[str, Any] | None,
) -> dict[str, str] | None:
    """For single-pass generation pick one role-specific LLM config as override.

    Priority: `host` if present in map, otherwise the first voice from `voices` found in map,
    otherwise the first valid entry in the map.
    """
    if not isinstance(role_llm_map, dict) or not role_llm_map:
        return None

    def _extract(cfg: Any) -> dict[str, str] | None:
        if hasattr(cfg, "model_dump"):
            cfg = cfg.model_dump()
        if not isinstance(cfg, dict):
            return None
        model = str(cfg.get("model") or "").strip()
        base_url = str(cfg.get("base_url") or "").strip()
        if not model:
            return None
        out = {"model": model}
        if base_url:
            out["base_url"] = base_url
        return out

    if "host" in role_llm_map:
        host_cfg = _extract(role_llm_map.get("host"))
        if host_cfg:
            return host_cfg
    for role in voices:
        cfg = _extract(role_llm_map.get(role))
        if cfg:
            return cfg
    for cfg in role_llm_map.values():
        extracted = _extract(cfg)
        if extracted:
            return extracted
    return None


def _resolve_primary_role_llm_choice(
    voices: list[str],
    role_llm_map: dict[str, Any] | None,
) -> tuple[str | None, dict[str, str] | None]:
    """Return (role_name, override_cfg) for single-pass role=model mode."""
    if not isinstance(role_llm_map, dict) or not role_llm_map:
        return None, None

    if "host" in role_llm_map:
        cfg = _resolve_primary_role_llm_override(["host"], {"host": role_llm_map.get("host")})
        if cfg:
            return "host", cfg

    for role in voices:
        cfg = _resolve_primary_role_llm_override([role], {role: role_llm_map.get(role)})
        if cfg:
            return role, cfg

    for role, cfg_raw in role_llm_map.items():
        cfg = _resolve_primary_role_llm_override([str(role)], {str(role): cfg_raw})
        if cfg:
            return str(role), cfg
    return None, None


async def validate_primary_role_llm_preflight(
    voices: list[str],
    role_llm_map: dict[str, Any] | None,
) -> tuple[str | None, dict[str, str] | None]:
    """Validate and return primary single-pass role/model override.

    Checks endpoint reachability and (best-effort) model presence via `/models`.
    """
    role_name, override = _resolve_primary_role_llm_choice(voices, role_llm_map)
    if not override:
        return role_name, override

    model = str(override.get("model") or "").strip()
    base_url = str(override.get("base_url") or "").strip() or None
    try:
        available_models = await llm_service.list_models(base_url=base_url)
    except llm_service.LMStudioError as e:
        role_hint = role_name or "неизвестная роль"
        raise ValueError(f"LLM endpoint для роли '{role_hint}' недоступен: {e}") from e

    if available_models and model and model not in available_models:
        role_hint = role_name or "неизвестная роль"
        sample = ", ".join(available_models[:8])
        suffix = "..." if len(available_models) > 8 else ""
        raise ValueError(
            f"Модель '{model}' для роли '{role_hint}' не найдена на endpoint. "
            f"Доступные модели: {sample}{suffix}"
        )
    return role_name, override


def validate_role_llm_map(
    voices: list[str],
    role_llm_map: dict[str, Any] | None,
) -> None:
    """Validate role-specific LLM config against current script roles."""
    if role_llm_map is None:
        return
    if not isinstance(role_llm_map, dict):
        raise ValueError("role_llm_map должен быть объектом вида { role: { model, base_url? } }.")

    known_roles = set(str(v).strip() for v in voices if str(v).strip())
    for role, cfg in role_llm_map.items():
        role_name = str(role).strip()
        if not role_name:
            raise ValueError("role_llm_map содержит пустое имя роли.")
        if role_name not in known_roles:
            raise ValueError(
                f"role_llm_map содержит неизвестную роль '{role_name}'. "
                f"Доступные роли: {', '.join(voices)}."
            )
        if hasattr(cfg, "model_dump"):
            cfg = cfg.model_dump()
        if not isinstance(cfg, dict):
            raise ValueError(f"role_llm_map['{role_name}'] должен быть объектом {{ model, base_url? }}.")
        model = str(cfg.get("model") or "").strip()
        if not model:
            raise ValueError(f"role_llm_map['{role_name}'].model не должен быть пустым.")


def _extract_role_llm_override(role: str, role_llm_map: dict[str, Any] | None) -> dict[str, str] | None:
    """Extract {model, base_url?} override for a specific role."""
    if not isinstance(role_llm_map, dict):
        return None
    cfg = role_llm_map.get(role)
    if hasattr(cfg, "model_dump"):
        cfg = cfg.model_dump()
    if not isinstance(cfg, dict):
        return None
    model = str(cfg.get("model") or "").strip()
    base_url = str(cfg.get("base_url") or "").strip()
    if not model:
        return None
    out = {"model": model}
    if base_url:
        out["base_url"] = base_url
    return out


async def validate_role_llm_map_preflight_all(
    voices: list[str],
    role_llm_map: dict[str, Any] | None,
) -> None:
    """Preflight all configured role overrides used in turn-taking mode."""
    if not isinstance(role_llm_map, dict) or not role_llm_map:
        return
    cache: dict[str | None, list[str]] = {}
    for role in voices:
        override = _extract_role_llm_override(role, role_llm_map)
        if not override:
            continue
        model = str(override.get("model") or "").strip()
        base_url = str(override.get("base_url") or "").strip() or None
        if base_url not in cache:
            try:
                cache[base_url] = await llm_service.list_models(base_url=base_url)
            except llm_service.LMStudioError as e:
                raise ValueError(f"LLM endpoint для роли '{role}' недоступен: {e}") from e
        available = cache[base_url]
        if available and model not in available:
            sample = ", ".join(available[:8])
            suffix = "..." if len(available) > 8 else ""
            raise ValueError(
                f"Модель '{model}' для роли '{role}' не найдена на endpoint. "
                f"Доступные модели: {sample}{suffix}"
            )


def _script_prompts(
    document_id: str,
    minutes: int = 5,
    style: str = "conversational",
    focus: str | None = None,
    voices: list[str] | None = None,
    scenario: str = "classic_overview",
    scenario_options: dict[str, Any] | None = None,
    tts_friendly: bool = True,
    knowledge_mode: str | None = None,
    return_debug: bool = False,
) -> tuple[str, str, list[str]] | tuple[str, str, list[str], dict[str, Any]]:
    """Build (system, user, voice_list) for podcast script. Raises ValueError if no chunks."""
    voice_list = voices or ["host", "guest1", "guest2"]
    opts = scenario_options or {}
    scenario_key, scenario_meta = _resolve_script_scenario(scenario)
    _validate_scenario_roles(scenario_key, voice_list)
    effective_knowledge_mode = _effective_script_knowledge_mode(scenario_key, knowledge_mode)
    ctx_top_k = _safe_int(opts.get("doc_context_chunks"), 5 if tts_friendly else 7, min_value=2, max_value=10)
    style_name, style_hint = _style_instruction(style)
    scenario_label = str(scenario_meta.get("label", scenario_key))
    retrieval_query = _build_podcast_retrieval_query(
        document_id,
        scenario_label=scenario_label,
        style_name=style_name,
        focus=focus,
    )
    chunks = rag_service.retrieve(document_id, retrieval_query, top_k=ctx_top_k)
    if not chunks:
        raise ValueError(f"No indexed content for document {document_id}. Run ingest first.")
    ctx_chars_default = 4500 if tts_friendly else 8000
    ctx_chars = _safe_int(opts.get("doc_context_chars"), ctx_chars_default, min_value=800, max_value=20000)
    context = _compose_script_context(chunks, max_chars=ctx_chars, max_chunks=ctx_top_k, per_chunk_chars=1800)
    voices_str = ", ".join(voice_list)
    word_count = minutes * 150
    style_clause = f"Стиль: {style_name}. " + (f"Доп. требования стиля: {style_hint}" if style_hint else "")
    scenario_clause_ru = (
        f"Сценарий: {scenario_label}. {scenario_meta.get('prompt_ru', '')}"
    ).strip()
    scenario_clause_en = (
        f"Scenario: {scenario_label}. {scenario_meta.get('prompt_en', '')}"
    ).strip()
    scenario_extra_ru, scenario_extra_en = _scenario_extra_guidance(scenario_key, scenario_options, voice_list)
    knowledge_ru = ""
    knowledge_en = ""
    if effective_knowledge_mode == "hybrid_model":
        knowledge_ru = (
            "Допускается добавлять внешнюю профессиональную критику, best practices, альтернативы и предложения, "
            "но все внешние мысли нужно явно помечать в начале реплики префиксом 'Вне документа:' или 'Критика модели:'. "
            "Нельзя выдавать внешние идеи за содержание документа. "
        )
        knowledge_en = (
            "External professional critique, best practices, alternatives, and recommendations are allowed, "
            "but every external thought must start with 'Вне документа:' or 'Критика модели:'. "
            "Do not present external ideas as if they came from the document. "
        )
    scenario_extra_ru_clause = f"{scenario_extra_ru}\n" if scenario_extra_ru else ""
    if len(voice_list) > 1:
        opening_rule_ru = (
            "Начало скрипта: сначала короткое введение в тему и контекст выпуска, "
            "затем представление участников (гостей/спикеров) и их роли в разговоре. "
            "Не начинай сразу с узких деталей документа без подводки.\n"
        )
        opening_rule_en = (
            "Start of the script: first provide a short introduction to the topic and episode context, "
            "then introduce the participants (guests/speakers) and their role in the conversation. "
            "Do not start immediately with narrow details from the document. "
        )
    else:
        opening_rule_ru = (
            "Начало скрипта: сначала короткое введение в тему и цель выпуска, "
            "потом переход к разбору материала. Не начинай сразу с узких деталей без подводки.\n"
        )
        opening_rule_en = (
            "Start of the script: first provide a short introduction to the topic and episode goal, "
            "then move into the material. Do not start immediately with narrow details without context. "
        )

    if tts_friendly:
        # TTS-friendly режим: максимально жёстко просим LLM ставить ударения и транскрипцию
        system = (
            "Ты сценарист подкаста. Скрипт будет использован для синтеза речи (TTS, Silero).\n"
            "Пиши ВЕСЬ текст реплик строго на русском языке.\n"
            f"Нужно написать скрипт подкаста на {minutes} минут (~{word_count} слов) "
            f"с количеством говорящих {len(voice_list)}: {voices_str}.\n"
            f"{style_clause}\n"
            f"{scenario_clause_ru}\n"
            f"{scenario_extra_ru_clause}"
            f"{knowledge_ru}"
            f"{opening_rule_ru}"
            "Сценарий обсуждает материал ниже.\n\n"
            "ОЧЕНЬ ВАЖНО: формат текста для синтеза речи (следуй строго каждому пункту):\n"
            "1) УДАРЕНИЕ (обязательно): ставь знак плюс + СРАЗУ ПОСЛЕ ударной гласной в слове.\n"
            "   Примеры: \"Пот+ом\", \"г+етрах\", \"к+едров\", \"систем+а\".\n"
            "2) АНГЛИЙСКИЕ СЛОВА И СОКРАЩЕНИЯ: всегда пиши по-русски, как они произносятся, "
            "с возможным ударением через +. Примеры: Google → \"Гу+гл\", SQL → \"эс-кью-э+л\".\n"
            "   НИКОГДА не оставляй латинские буквы A–Z в тексте.\n"
            "3) ЧИСЛА: никогда не используй цифры 0-9 в тексте реплик. "
            "   Любые числа, годы, проценты и диапазоны пиши словами по-русски.\n"
            "4) НЕ используй технические маркеры и спец-теги в тексте "
            "(например [PAUSE_*], SSML и любые квадратные скобки).\n\n"
            "Формат вывода:\n"
            "- ВЫВОДИ ТОЛЬКО один JSON-массив объектов с ключами \"voice\" и \"text\".\n"
            "- НЕ пиши никакого текста до или после JSON.\n"
            "Пример корректного вывода:\n"
            "[\n"
            "  {\"voice\": \"host\", \"text\": \"Добрый д+ень! Пот+ом мы обсудим Гу+гл и эс-кью-э+л.\"},\n"
            "  {\"voice\": \"guest1\", \"text\": \"Спас+ибо за приглаш+ение! Это очень интересн+ая т+ема.\"}\n"
            "]\n"
            "Каждая строка \"text\" ДОЛЖНА содержать ударения с +, не содержать латинских букв и не содержать цифр."
        )
    else:
        system = (
            "You are a podcast scriptwriter. "
            "Write the entire script in Russian. "
            f"Write a podcast script for {minutes} minutes (~{word_count} words) "
            f"with {len(voice_list)} speakers: {voices_str}. "
            f"Style guidance: {style_name}. {style_hint} "
            f"{scenario_clause_en} "
            f"{scenario_extra_en} "
            f"{knowledge_en} "
            f"{opening_rule_en}"
            "The script discusses the material below. All dialogue must be in Russian.\n\n"
            "Output ONLY a JSON array of objects, each with keys \"voice\" and \"text\". "
            "Example: [{\"voice\":\"host\",\"text\":\"Добрый день...\"},{\"voice\":\"guest1\",\"text\":\"Спасибо за приглашение...\"}]\n"
            "Do NOT include any text before or after the JSON array. All \"text\" values must be in Russian."
        )
    user = f"Material:\n\n{context}"
    if not return_debug:
        return system, user, voice_list
    debug = {
        "mode": "single_pass",
        "retrieval_query": retrieval_query,
        "chunks_selected": len(chunks),
        "chunk_limit": int(ctx_top_k),
        "doc_context_chars": len(context),
        "doc_context_char_limit": int(ctx_chars),
        "tts_friendly": bool(tts_friendly),
        "knowledge_mode": _normalize_knowledge_mode(knowledge_mode),
        "effective_knowledge_mode": effective_knowledge_mode,
        "approx_prompt_chars": len(system) + len(user),
    }
    return system, user, voice_list, debug


def _normalize_generation_mode(mode: str | None) -> str:
    key = str(mode or "single_pass").strip().lower()
    if key not in {"single_pass", "turn_taking"}:
        key = "single_pass"
    return key


def get_script_generation_debug(
    document_id: str,
    *,
    minutes: int = 5,
    style: str = "conversational",
    focus: str | None = None,
    voices: list[str] | None = None,
    scenario: str = "classic_overview",
    scenario_options: dict[str, Any] | None = None,
    generation_mode: str = "single_pass",
    tts_friendly: bool = True,
    knowledge_mode: str | None = None,
) -> dict[str, Any]:
    """Estimate retrieval/prompt context usage for UI diagnostics."""
    mode = _normalize_generation_mode(generation_mode)
    voice_list = voices or ["host", "guest1", "guest2"]
    scenario_key, scenario_meta = _resolve_script_scenario(scenario)
    _validate_scenario_roles(scenario_key, voice_list)
    effective_knowledge_mode = _effective_script_knowledge_mode(scenario_key, knowledge_mode)
    opts = scenario_options or {}
    style_name, style_hint = _style_instruction(style)
    scenario_label = str(scenario_meta.get("label", scenario_key))
    retrieval_query = _build_podcast_retrieval_query(
        document_id,
        scenario_label=scenario_label,
        style_name=style_name,
        focus=focus,
    )

    if mode == "turn_taking":
        ctx_top_k = _safe_int(opts.get("doc_context_chunks"), 4, min_value=2, max_value=8)
        chunks = rag_service.retrieve(document_id, retrieval_query, top_k=ctx_top_k)
        if not chunks:
            raise ValueError(f"No indexed content for document {document_id}. Run ingest first.")
        turn_doc_context_chars = _safe_int(opts.get("doc_context_chars"), 1800, min_value=500, max_value=12000)
        outline_context_chars = _safe_int(
            opts.get("outline_context_chars"),
            max(turn_doc_context_chars, 2200),
            min_value=600,
            max_value=16000,
        )
        context = _compose_script_context(chunks, max_chars=turn_doc_context_chars, max_chunks=ctx_top_k, per_chunk_chars=1000)
        outline_context = _compose_script_context(chunks, max_chars=outline_context_chars, max_chunks=ctx_top_k, per_chunk_chars=1200)
        sequence = _turn_taking_role_sequence(scenario_key, voice_list, minutes, opts)
        scenario_prompt_ru = str(scenario_meta.get("prompt_ru", "") or "")
        scenario_extra_ru, _scenario_extra_en = _scenario_extra_guidance(scenario_key, scenario_options, voice_list)
        tt_system, tt_user = _turn_taking_prompts(
            context=context,
            style_name=style_name,
            style_hint=style_hint,
            scenario_label=scenario_label,
            scenario_prompt_ru=scenario_prompt_ru,
            scenario_extra_ru=scenario_extra_ru,
            voice_list=voice_list,
            role=sequence[0] if sequence else (voice_list[0] if voice_list else "host"),
            history=[],
            turn_index=0,
            total_turns=max(1, len(sequence)),
            target_words=max(12, int(max(120, minutes * 150) / max(1, len(sequence) or 1))),
        )
        return {
            "mode": "turn_taking",
            "retrieval_query": retrieval_query,
            "chunks_selected": len(chunks),
            "chunk_limit": int(ctx_top_k),
            "doc_context_chars": len(context),
            "doc_context_char_limit": int(turn_doc_context_chars),
            "outline_context_chars": len(outline_context),
            "outline_context_char_limit": int(outline_context_chars),
            "planned_turns": len(sequence),
            "knowledge_mode": _normalize_knowledge_mode(knowledge_mode),
            "effective_knowledge_mode": effective_knowledge_mode,
            "approx_prompt_chars": len(tt_system) + len(tt_user),
        }

    ctx_top_k = _safe_int(opts.get("doc_context_chunks"), 5 if tts_friendly else 7, min_value=2, max_value=10)
    chunks = rag_service.retrieve(document_id, retrieval_query, top_k=ctx_top_k)
    if not chunks:
        raise ValueError(f"No indexed content for document {document_id}. Run ingest first.")
    ctx_chars_default = 4500 if tts_friendly else 8000
    ctx_chars = _safe_int(opts.get("doc_context_chars"), ctx_chars_default, min_value=800, max_value=20000)
    context = _compose_script_context(chunks, max_chars=ctx_chars, max_chunks=ctx_top_k, per_chunk_chars=1800)
    return {
        "mode": "single_pass",
        "retrieval_query": retrieval_query,
        "chunks_selected": len(chunks),
        "chunk_limit": int(ctx_top_k),
        "doc_context_chars": len(context),
        "doc_context_char_limit": int(ctx_chars),
        "tts_friendly": bool(tts_friendly),
        "knowledge_mode": _normalize_knowledge_mode(knowledge_mode),
        "effective_knowledge_mode": effective_knowledge_mode,
    }


def _turn_taking_role_sequence(
    scenario: str,
    voices: list[str],
    minutes: int,
    scenario_options: dict[str, Any] | None,
) -> list[str]:
    opts = scenario_options or {}
    if not voices:
        return []
    default_turns = min(32, max(len(voices) * 4, minutes * 3))
    max_turns = _safe_int(opts.get("max_turns"), default_turns, min_value=max(len(voices), 2), max_value=40)
    scenario_key, _ = _resolve_script_scenario(scenario)

    if scenario_key == "news_digest":
        block_count = _safe_int(opts.get("block_count"), default=4, min_value=2, max_value=12)
        primary = voices[0]
        turns = min(max_turns, max(2, block_count))
        return [primary for _ in range(turns)]

    if scenario_key == "interview" and len(voices) >= 2:
        host = voices[0]
        guests = voices[1:] or [host]
        seq: list[str] = []
        guest_idx = 0
        for i in range(max_turns):
            if i % 2 == 0:
                seq.append(host)
            else:
                seq.append(guests[guest_idx % len(guests)])
                guest_idx += 1
        return seq

    if scenario_key == "debate" and len(voices) >= 3:
        moderator = voices[0]
        debaters = voices[1:]
        seq = [moderator]
        i = 1
        while len(seq) < max_turns - 1:
            seq.append(debaters[(i - 1) % len(debaters)])
            i += 1
            if len(seq) < max_turns - 1 and (len(seq) % 5 == 0):
                seq.append(moderator)
        if len(seq) < max_turns:
            seq.append(moderator)
        return seq[:max_turns]

    # Generic round-robin
    return [voices[i % len(voices)] for i in range(max_turns)]


def _match_voice_label(label: Any, voices: list[str]) -> str | None:
    raw = str(label or "").strip()
    if not raw:
        return None
    low = raw.lower()
    for v in voices:
        if raw == v or low == str(v).lower():
            return v
    return None


def _outline_default_blocks(scenario: str, voices: list[str], total_turns: int) -> list[dict[str, Any]]:
    """Fallback deterministic outline if LLM outline fails."""
    total = max(2, total_turns)
    first_role = voices[0]
    if scenario == "interview" and len(voices) >= 2:
        intro_roles = [voices[0], voices[1]]
        core_roles = [voices[0], *voices[1:]]
        return [
            {
                "title": "Вступление и представление гостей",
                "goal": "Обозначить тему выпуска и представить участников.",
                "instruction": "Короткое введение и представление ролей гостей.",
                "role_order": intro_roles,
                "target_turns": min(3, total),
            },
            {
                "title": "Основные вопросы по материалу",
                "goal": "Разобрать ключевые тезисы документа через вопросы и ответы.",
                "instruction": "Ведущий задаёт конкретные вопросы, гости отвечают по документу.",
                "role_order": core_roles,
                "target_turns": max(1, total - min(3, total) - 1),
            },
            {
                "title": "Итог",
                "goal": "Подвести итог и выделить главное.",
                "instruction": "Короткое резюме и финальный вывод.",
                "role_order": [first_role],
                "target_turns": 1,
            },
        ]
    if scenario == "debate" and len(voices) >= 3:
        moderator = voices[0]
        debaters = voices[1:]
        return [
            {
                "title": "Вступление и рамка дебатов",
                "goal": "Представить тему и позиции участников.",
                "instruction": "Модератор представляет тему и участников, задаёт рамку спора.",
                "role_order": [moderator, *debaters[:1]],
                "target_turns": min(3, total),
            },
            {
                "title": "Аргументы и контраргументы",
                "goal": "Сравнить позиции на основе документа.",
                "instruction": "Спикеры спорят по тезисам документа, модератор удерживает тему.",
                "role_order": [*debaters, moderator],
                "target_turns": max(1, total - min(3, total) - 1),
            },
            {
                "title": "Резюме дебатов",
                "goal": "Суммировать сильные аргументы и вывод.",
                "instruction": "Модератор и участники кратко завершают обсуждение.",
                "role_order": [moderator, debaters[0]],
                "target_turns": 1,
            },
        ]
    return [
        {
            "title": "Вступление",
            "goal": "Ввести тему выпуска и представить участников.",
            "instruction": "Короткое введение с подводкой к документу.",
            "role_order": [first_role],
            "target_turns": min(2, total),
        },
        {
            "title": "Разбор ключевых идей",
            "goal": "Разобрать основные идеи и факты документа.",
            "instruction": "Идти по ключевым тезисам связно, с реакцией участников.",
            "role_order": voices,
            "target_turns": max(1, total - min(2, total) - 1),
        },
        {
            "title": "Выводы",
            "goal": "Подвести краткие итоги обсуждения.",
            "instruction": "Сделать короткое резюме без повторов.",
            "role_order": [first_role],
            "target_turns": 1,
        },
    ]


def _normalize_turn_outline(
    raw_outline: Any,
    *,
    voices: list[str],
    total_turns: int,
    scenario_key: str,
) -> dict[str, Any]:
    """Normalize LLM outline JSON into bounded blocks."""
    if isinstance(raw_outline, list):
        raw_outline = {"blocks": raw_outline}
    if not isinstance(raw_outline, dict):
        raise ValueError("Outline должен быть JSON-объектом с полем blocks.")

    blocks_raw = raw_outline.get("blocks")
    if not isinstance(blocks_raw, list) or not blocks_raw:
        raise ValueError("Outline не содержит blocks[].")

    normalized_blocks: list[dict[str, Any]] = []
    for i, item in enumerate(blocks_raw[:10]):
        if not isinstance(item, dict):
            continue
        title = str(
            item.get("title")
            or item.get("name")
            or item.get("section")
            or item.get("block")
            or f"Блок {i + 1}"
        ).strip()
        goal = str(item.get("goal") or item.get("objective") or item.get("focus") or "").strip()
        instruction = str(item.get("instruction") or item.get("prompt") or item.get("notes") or goal).strip()
        turns = _safe_int(item.get("target_turns"), default=2, min_value=1, max_value=12)
        role_order_raw = item.get("role_order", item.get("roles", item.get("speakers")))
        role_order: list[str] = []
        if isinstance(role_order_raw, str):
            for token in re.split(r"[,;/|]+", role_order_raw):
                matched = _match_voice_label(token, voices)
                if matched and matched not in role_order:
                    role_order.append(matched)
        elif isinstance(role_order_raw, list):
            for token in role_order_raw:
                matched = _match_voice_label(token, voices)
                if matched and matched not in role_order:
                    role_order.append(matched)
        if not role_order:
            role_order = [voices[i % len(voices)]]
        normalized_blocks.append(
            {
                "title": title or f"Блок {i + 1}",
                "goal": goal,
                "instruction": instruction or "Продолжай разбор документа по теме блока.",
                "role_order": role_order,
                "target_turns": turns,
            }
        )

    if not normalized_blocks:
        raise ValueError("Outline не удалось нормализовать: нет валидных блоков.")

    host_name = str(voices[0]).strip() if voices else "host"
    guest_names = [str(v).strip() for v in voices[1:] if str(v).strip()]
    if voices:
        # Preserve participant mapping from UI order in the intro block.
        normalized_blocks[0]["role_order"] = list(voices)

    # Force intro/conclusion shape to improve structure.
    intro_parts = ["Начни с введения в тему."]
    if host_name:
        intro_parts.append(f"Ведущий {host_name} сначала представляется как ведущий.")
    if guest_names:
        if len(guest_names) == 1:
            intro_parts.append(f"Затем представь гостя {guest_names[0]}.")
        else:
            intro_parts.append(f"Затем представь гостей: {', '.join(guest_names)}.")
    intro_parts.append("Не называй ведущего гостем и не пропускай участников.")
    intro_parts.append("После этого плавно перейди к первому тезису.")
    normalized_blocks[0]["instruction"] = (
        " ".join(intro_parts) + " " + str(normalized_blocks[0].get("instruction", "")).strip()
    ).strip()
    if len(normalized_blocks) > 1:
        normalized_blocks[-1]["instruction"] = (
            str(normalized_blocks[-1].get("instruction", "")).strip()
            + " Заверши обсуждение коротким итогом."
        ).strip()

    target_total = max(2, total_turns)
    current_total = sum(_safe_int(b.get("target_turns"), default=1, min_value=1, max_value=12) for b in normalized_blocks)
    if current_total <= 0:
        current_total = len(normalized_blocks)
    scaled: list[int] = []
    for b in normalized_blocks:
        turns = _safe_int(b.get("target_turns"), default=1, min_value=1, max_value=12)
        scaled_turns = max(1, round(turns * target_total / current_total))
        scaled.append(scaled_turns)
    diff = target_total - sum(scaled)
    idx = 0
    guard = 0
    while diff != 0 and guard < 100:
        j = idx % len(scaled)
        if diff > 0:
            scaled[j] += 1
            diff -= 1
        elif scaled[j] > 1:
            scaled[j] -= 1
            diff += 1
        idx += 1
        guard += 1
        if diff < 0 and all(x <= 1 for x in scaled):
            break
    for b, turns in zip(normalized_blocks, scaled):
        b["target_turns"] = max(1, int(turns))

    return {
        "episode_goal": str(raw_outline.get("episode_goal") or raw_outline.get("goal") or "").strip(),
        "blocks": normalized_blocks,
        "scenario": scenario_key,
    }


def _parse_turn_outline_json(raw: str, *, voices: list[str], total_turns: int, scenario_key: str) -> dict[str, Any]:
    s = str(raw or "").strip()
    if not s:
        raise ValueError("Пустой outline-ответ от LLM.")
    s = s.replace("“", '"').replace("”", '"').replace("„", '"').replace("«", '"').replace("»", '"')
    s = s.replace("’", "'")
    blob = None
    m_obj = re.search(r"\{[\s\S]*\}", s)
    if m_obj:
        blob = m_obj.group()
    else:
        m_arr = re.search(r"\[[\s\S]*\]", s)
        if m_arr:
            blob = m_arr.group()
    if blob is None:
        raise ValueError("LLM не вернула JSON outline.")
    try:
        parsed = json.loads(blob)
    except json.JSONDecodeError as e:
        raise ValueError(f"Не удалось разобрать JSON outline: {e.msg}") from e
    return _normalize_turn_outline(parsed, voices=voices, total_turns=total_turns, scenario_key=scenario_key)


def _format_turn_outline_summary(outline: dict[str, Any], max_blocks: int = 8, max_chars: int = 1400) -> str:
    blocks = list(outline.get("blocks") or [])[:max_blocks]
    if not blocks:
        return "(план выпуска не задан)"
    parts: list[str] = []
    for i, b in enumerate(blocks, start=1):
        title = str(b.get("title") or f"Блок {i}")
        roles = ", ".join(str(r) for r in (b.get("role_order") or []))
        turns = _safe_int(b.get("target_turns"), default=1, min_value=1, max_value=12)
        goal = _truncate_prompt_text(str(b.get("goal") or b.get("instruction") or ""), 140)
        parts.append(f"{i}. {title} [{turns} ход.] роли: {roles}. Цель: {goal}")
    return _truncate_prompt_text("\n".join(parts), max_chars)


def _expand_turn_outline_to_plan(
    outline: dict[str, Any],
    *,
    voices: list[str],
    fallback_sequence: list[str],
) -> list[dict[str, Any]]:
    """Expand outline blocks into per-turn plan, preserving bounded total turns."""
    blocks = list(outline.get("blocks") or [])
    if not blocks:
        return [{"role": role} for role in fallback_sequence]

    plan: list[dict[str, Any]] = []
    total_blocks = len(blocks)
    for block_idx, block in enumerate(blocks, start=1):
        role_order = [r for r in (block.get("role_order") or []) if r in voices] or [voices[0]]
        block_turns = _safe_int(block.get("target_turns"), default=1, min_value=1, max_value=12)
        for j in range(block_turns):
            role = role_order[j % len(role_order)]
            plan.append(
                {
                    "role": role,
                    "block_index": block_idx,
                    "blocks_total": total_blocks,
                    "block_title": str(block.get("title") or f"Блок {block_idx}"),
                    "block_goal": str(block.get("goal") or ""),
                    "block_instruction": str(block.get("instruction") or ""),
                    "block_turn_index": j + 1,
                    "block_turns": block_turns,
                }
            )

    if fallback_sequence:
        target_total = len(fallback_sequence)
        if len(plan) > target_total:
            plan = plan[:target_total]
        elif len(plan) < target_total:
            for role in fallback_sequence[len(plan):]:
                plan.append({"role": role})
    return plan


def _turn_outline_prompts(
    *,
    context: str,
    style_name: str,
    style_hint: str,
    scenario_label: str,
    scenario_prompt_ru: str,
    scenario_extra_ru: str,
    voices: list[str],
    target_turns: int,
    minutes: int,
    effective_knowledge_mode: str = "document_only",
) -> tuple[str, str]:
    roles_str = ", ".join(voices)
    host_name = str(voices[0]).strip() if voices else "host"
    guest_names = [str(v).strip() for v in voices[1:] if str(v).strip()]
    guests_str = ", ".join(guest_names)
    max_blocks = min(8, max(3, minutes + 1))
    knowledge_note = ""
    if effective_knowledge_mode == "hybrid_model":
        knowledge_note = (
            "\nМожно добавлять внешние профессиональные гипотезы и критику сверх документа, "
            "но такие пункты плана должны явно указывать, что это вне документа."
        )
    system = (
        "Ты редактор структуры подкаста. Сначала спланируй выпуск, не пиши полный сценарий.\n"
        "Ответь ТОЛЬКО JSON-объектом без markdown и комментариев.\n"
        "Сформируй поле blocks: массив блоков плана. Каждый блок должен иметь: "
        "\"title\", \"goal\", \"instruction\", \"role_order\" (массив ролей), \"target_turns\" (число).\n"
        "Используй роли ТОЛЬКО из переданного списка roles.\n"
        "Первый блок обязан быть вступлением с представлением участников. Последний блок — краткий итог.\n"
        "Считай, что первый участник списка roles — ведущий (host/moderator), остальные — гости/спикеры."
        + knowledge_note
    )
    user = (
        f"Сценарий: {scenario_label}. {scenario_prompt_ru}\n"
        f"{(scenario_extra_ru + chr(10)) if scenario_extra_ru else ''}"
        f"Стиль: {style_name}. {style_hint}\n"
        f"Длительность выпуска: {minutes} мин.\n"
        f"Роли (используй эти значения дословно): {roles_str}\n"
        f"Ведущий: {host_name}\n"
        f"{f'Гости/спикеры: {guests_str}' if guests_str else 'Гостей нет (монолог ведущего)'}\n"
        f"Ориентир по общему числу ходов: {target_turns}\n"
        f"Сделай {max_blocks if max_blocks <= 4 else f'3-{max_blocks}'} блоков, покрывающих весь выпуск.\n\n"
        "Материал документа (сжатый контекст):\n"
        f"{_truncate_prompt_text(context, 9000)}\n\n"
        "Важно для первого блока: ведущий представляется как ведущий, затем представляет всех гостей/спикеров по именам; "
        "не называй ведущего гостем и не пропускай участников.\n\n"
        "Верни JSON вида:\n"
        "{\n"
        '  "episode_goal": "...",\n'
        '  "blocks": [\n'
        '    {"title":"...", "goal":"...", "instruction":"...", "role_order":["host","guest1"], "target_turns":2}\n'
        "  ]\n"
        "}"
    )
    return system, user


async def _generate_turn_taking_outline(
    *,
    context: str,
    style_name: str,
    style_hint: str,
    scenario_key: str,
    scenario_label: str,
    scenario_prompt_ru: str,
    scenario_extra_ru: str,
    voices: list[str],
    minutes: int,
    target_turns: int,
    role_llm_map: dict[str, Any] | None = None,
    context_char_budget: int = 2400,
    effective_knowledge_mode: str = "document_only",
) -> dict[str, Any]:
    """Generate JSON outline for turn-taking; raises ValueError on invalid outline."""
    primary_role = voices[0] if voices else "host"
    outline_override = _extract_role_llm_override(primary_role, role_llm_map)
    attempt_caps = [max(800, context_char_budget), 1600, 1000]
    last_exc: Exception | None = None
    for cap in attempt_caps:
        try:
            system, user = _turn_outline_prompts(
                context=_truncate_prompt_text(context, cap),
                style_name=style_name,
                style_hint=style_hint,
                scenario_label=scenario_label,
                scenario_prompt_ru=scenario_prompt_ru,
                scenario_extra_ru=scenario_extra_ru,
                voices=voices,
                target_turns=target_turns,
                minutes=minutes,
                effective_knowledge_mode=effective_knowledge_mode,
            )
            raw = await llm_service.chat_completion(
                system,
                user,
                temperature=0.2,
                max_tokens=1600,
                model=(outline_override or {}).get("model"),
                base_url=(outline_override or {}).get("base_url"),
            )
            return _parse_turn_outline_json(raw, voices=voices, total_turns=target_turns, scenario_key=scenario_key)
        except llm_service.LMStudioError as e:
            last_exc = e
            if not _is_context_overflow_error(e):
                raise
            logger.warning("Outline prompt overflow; retrying with smaller context cap=%s", cap)
            continue
    if last_exc is not None:
        raise last_exc
    raise ValueError("Не удалось построить outline.")


async def generate_podcast_script_outline(
    document_id: str,
    *,
    minutes: int = 5,
    style: str = "conversational",
    focus: str | None = None,
    voices: list[str] | None = None,
    scenario: str = "classic_overview",
    scenario_options: dict[str, Any] | None = None,
    role_llm_map: dict[str, Any] | None = None,
    knowledge_mode: str | None = None,
) -> dict[str, Any]:
    """Generate a normalized outline plan for podcast turn-taking."""
    voice_list = voices or ["host", "guest1", "guest2"]
    scenario_key, scenario_meta = _resolve_script_scenario(scenario)
    _validate_scenario_roles(scenario_key, voice_list)
    effective_knowledge_mode = _effective_script_knowledge_mode(scenario_key, knowledge_mode)
    validate_role_llm_map(voice_list, role_llm_map)
    # Single primary preflight is enough for outline (one request).
    await validate_primary_role_llm_preflight(voice_list, role_llm_map)

    opts = scenario_options or {}
    ctx_top_k = _safe_int(opts.get("doc_context_chunks"), 4, min_value=2, max_value=8)
    style_name, style_hint = _style_instruction(style)
    scenario_label = str(scenario_meta.get("label", scenario_key))
    retrieval_query = _build_podcast_retrieval_query(
        document_id,
        scenario_label=scenario_label,
        style_name=style_name,
        focus=focus,
    )
    chunks = rag_service.retrieve(document_id, retrieval_query, top_k=ctx_top_k)
    if not chunks:
        raise ValueError(f"No indexed content for document {document_id}. Run ingest first.")
    outline_ctx_chars = _safe_int(opts.get("outline_context_chars"), 2200, min_value=600, max_value=12000)
    context = _compose_script_context(chunks, max_chars=outline_ctx_chars, max_chunks=ctx_top_k, per_chunk_chars=1200)
    scenario_prompt_ru = str(scenario_meta.get("prompt_ru", "") or "")
    scenario_extra_ru, _ = _scenario_extra_guidance(scenario_key, scenario_options, voice_list)
    sequence = _turn_taking_role_sequence(scenario_key, voice_list, minutes, scenario_options)
    try:
        return await _generate_turn_taking_outline(
            context=context,
            style_name=style_name,
            style_hint=style_hint,
            scenario_key=scenario_key,
            scenario_label=scenario_label,
            scenario_prompt_ru=scenario_prompt_ru,
            scenario_extra_ru=scenario_extra_ru,
            voices=voice_list,
            minutes=minutes,
            target_turns=len(sequence),
            role_llm_map=role_llm_map,
            context_char_budget=outline_ctx_chars,
            effective_knowledge_mode=effective_knowledge_mode,
        )
    except Exception as e:
        logger.warning("Outline endpoint failed to get LLM outline; using deterministic fallback: %s", e)
        return {
            "scenario": scenario_key,
            "episode_goal": "",
            "blocks": _outline_default_blocks(scenario_key, voice_list, len(sequence)),
            "effective_knowledge_mode": effective_knowledge_mode,
        }


def _truncate_prompt_text(text: str, max_chars: int) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    if max_chars <= 0:
        return ""
    if len(s) <= max_chars:
        return s
    return s[: max(1, max_chars - 1)].rstrip() + "…"


def _format_turn_history(
    lines: list[DialogueLine],
    *,
    max_lines: int = 10,
    recent_char_budget: int = 2600,
    summary_char_budget: int = 900,
    per_line_char_limit: int = 240,
) -> str:
    """Return bounded turn history for prompts to avoid context growth."""
    if not lines:
        return "(пока нет истории)"

    recent_rev: list[DialogueLine] = []
    used_chars = 0
    for ln in reversed(lines):
        if len(recent_rev) >= max_lines:
            break
        rendered = f"{ln.voice}: {_truncate_prompt_text(ln.text, per_line_char_limit)}"
        rendered_len = len(rendered) + 1
        if recent_rev and used_chars + rendered_len > recent_char_budget:
            break
        recent_rev.append(ln)
        used_chars += rendered_len

    recent = list(reversed(recent_rev))
    older_count = max(0, len(lines) - len(recent))
    parts: list[str] = []

    if older_count > 0:
        older = lines[:older_count]
        role_counts: dict[str, int] = {}
        for ln in older:
            role_counts[ln.voice] = role_counts.get(ln.voice, 0) + 1
        counts_text = ", ".join(f"{role}: {cnt}" for role, cnt in role_counts.items())
        sample_lines = older[-3:] if older else []
        samples = " | ".join(
            f"{ln.voice}: {_truncate_prompt_text(ln.text, 120)}" for ln in sample_lines
        )
        summary = (
            f"(Сжатая ранняя история: {older_count} реплик. "
            f"По ролям: {counts_text}. "
            f"Последние реплики из ранней части: {samples})"
        )
        parts.append(_truncate_prompt_text(summary, summary_char_budget))

    recent_block = "\n".join(
        f"{ln.voice}: {_truncate_prompt_text(ln.text, per_line_char_limit)}" for ln in recent
    )
    parts.append(recent_block or "(пока нет истории)")
    return "\n".join(parts)


def _clean_turn_text(raw: str, role: str) -> str:
    s = str(raw or "").strip()
    if not s:
        return ""
    # Accept accidental JSON object/array and extract text.
    m = re.search(r"\{[\s\S]*\}", s)
    if m:
        try:
            obj = json.loads(m.group())
            if isinstance(obj, dict) and obj.get("text"):
                return str(obj.get("text")).strip()
        except Exception:
            pass
    m = re.search(r"\[[\s\S]*\]", s)
    if m:
        try:
            arr = json.loads(m.group())
            if isinstance(arr, list) and arr and isinstance(arr[0], dict) and arr[0].get("text"):
                return str(arr[0].get("text")).strip()
        except Exception:
            pass
    # Strip common prefixes like "host: ..."
    s = re.sub(rf"^\s*{re.escape(role)}\s*:\s*", "", s, flags=re.IGNORECASE)
    # Remove surrounding quotes if model returned a quoted string.
    if len(s) >= 2 and ((s[0] == s[-1] == '"') or (s[0] == s[-1] == "'")):
        s = s[1:-1].strip()
    return s


def _turn_word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", str(text or "")))


def _analyze_turn_text_quality(text: str, *, target_words: int) -> str | None:
    s = str(text or "").strip()
    if not s:
        return "empty"
    words = _turn_word_count(s)
    if words <= 2:
        return "too_short"
    # For normal turns we expect a complete phrase/sentence ending.
    # Allow short acknowledgements to pass with lower threshold.
    last_char = s[-1]
    if words >= max(8, int(target_words * 0.35)) and last_char not in ".!?…»)]":
        return "truncated"
    if last_char in ",:;(-—":
        return "truncated"
    return None


def _finalize_turn_text(text: str) -> str:
    s = str(text or "").strip()
    if not s:
        return s
    if s[-1] not in ".!?…»)]":
        s += "."
    return s


def _turn_taking_prompts(
    *,
    context: str,
    style_name: str,
    style_hint: str,
    scenario_label: str,
    scenario_prompt_ru: str,
    scenario_extra_ru: str,
    voice_list: list[str],
    role: str,
    history: list[DialogueLine],
    turn_index: int,
    total_turns: int,
    target_words: int,
    history_max_lines: int = 10,
    history_recent_char_budget: int = 2600,
    history_summary_char_budget: int = 900,
    outline_summary: str | None = None,
    current_block: dict[str, Any] | None = None,
    effective_knowledge_mode: str = "document_only",
) -> tuple[str, str]:
    roles_str = ", ".join(voice_list)
    history_block = _format_turn_history(
        history,
        max_lines=history_max_lines,
        recent_char_budget=history_recent_char_budget,
        summary_char_budget=history_summary_char_budget,
    )
    style_clause = f"Стиль: {style_name}. " + (f"Доп. требования стиля: {style_hint}" if style_hint else "")
    scenario_clause = f"Сценарий: {scenario_label}. {scenario_prompt_ru}".strip()
    extra_clause = f"{scenario_extra_ru}\n" if scenario_extra_ru else ""
    plan_clause = ""
    if outline_summary:
        plan_clause += f"План выпуска (кратко):\n{outline_summary}\n\n"
    if current_block:
        block_title = str(current_block.get("block_title") or "")
        block_goal = str(current_block.get("block_goal") or "").strip()
        block_instruction = str(current_block.get("block_instruction") or "").strip()
        block_idx = _safe_int(current_block.get("block_index"), default=1, min_value=1, max_value=99)
        blocks_total = _safe_int(current_block.get("blocks_total"), default=1, min_value=1, max_value=99)
        block_turn_idx = _safe_int(current_block.get("block_turn_index"), default=1, min_value=1, max_value=99)
        block_turns = _safe_int(current_block.get("block_turns"), default=1, min_value=1, max_value=99)
        plan_clause += (
            f"Текущий блок плана: {block_idx}/{blocks_total} — {block_title}\n"
            f"Ход внутри блока: {block_turn_idx}/{block_turns}\n"
            f"Цель блока: {block_goal or 'Сохраняй связность и раскрывай тему блока.'}\n"
            f"Инструкция блока: {block_instruction or 'Продолжай в рамках этого блока, не перепрыгивай к финалу.'}\n\n"
        )
    opening_turn_rule = ""
    if turn_index == 0:
        if len(voice_list) > 1:
            opening_turn_rule = (
                "Это начало выпуска. Сначала сделай короткое введение в тему и представь участников "
                "(гостей/спикеров) и их роли, затем задай направление обсуждения. "
                "Не начинай сразу с узкой детали из документа.\n"
            )
        else:
            opening_turn_rule = (
                "Это начало выпуска. Сначала сделай короткое введение в тему и цель выпуска, "
                "затем переходи к содержанию документа. Не начинай сразу с узкой детали.\n"
            )
    elif turn_index == 1 and len(voice_list) > 1:
        opening_turn_rule = (
            "Это ранняя часть выпуска сразу после вступления. Коротко отреагируй на введение и "
            "поддержи структуру разговора, не перескакивая резко в середину темы.\n"
        )
    system = (
        "Ты сценарист подкаста. Ты пишешь ТОЛЬКО ОДНУ следующую реплику для указанной роли.\n"
        "Пиши только на русском языке.\n"
        "Верни ТОЛЬКО текст реплики без JSON, без имени роли, без комментариев, без markdown.\n"
        "Реплика должна естественно продолжать предыдущие реплики и опираться на документ."
    )
    user = (
        f"{style_clause}\n"
        f"{scenario_clause}\n"
        f"{extra_clause}"
        f"{plan_clause}"
        f"{opening_turn_rule}"
        f"Все роли: {roles_str}\n"
        f"Текущая роль: {role}\n"
        f"Ход: {turn_index + 1} из {total_turns}\n"
        f"Ориентир длины этой реплики: ~{target_words} слов (допустимо +/- 50%).\n\n"
        f"История реплик:\n{history_block}\n\n"
        f"Материал документа:\n{context}\n\n"
        "Сгенерируй следующую реплику текущей роли."
    )
    return system, user


async def iter_podcast_script_turn_taking(
    document_id: str,
    *,
    minutes: int = 5,
    style: str = "conversational",
    focus: str | None = None,
    voices: list[str] | None = None,
    scenario: str = "classic_overview",
    scenario_options: dict[str, Any] | None = None,
    role_llm_map: dict[str, Any] | None = None,
    outline_plan: dict[str, Any] | None = None,
    knowledge_mode: str | None = None,
) -> AsyncGenerator[DialogueLine, None]:
    """Generate a script incrementally, one line per turn."""
    voice_list = voices or ["host", "guest1", "guest2"]
    scenario_key, scenario_meta = _resolve_script_scenario(scenario)
    effective_knowledge_mode = _effective_script_knowledge_mode(scenario_key, knowledge_mode)
    _validate_scenario_roles(scenario_key, voice_list)
    validate_role_llm_map(voice_list, role_llm_map)
    await validate_role_llm_map_preflight_all(voice_list, role_llm_map)

    tt_opts = scenario_options or {}
    ctx_top_k = _safe_int(tt_opts.get("doc_context_chunks"), 4, min_value=2, max_value=8)
    style_name, style_hint = _style_instruction(style)
    scenario_label = str(scenario_meta.get("label", scenario_key))
    retrieval_query = _build_podcast_retrieval_query(
        document_id,
        scenario_label=scenario_label,
        style_name=style_name,
        focus=focus,
    )
    chunks = rag_service.retrieve(document_id, retrieval_query, top_k=ctx_top_k)
    if not chunks:
        raise ValueError(f"No indexed content for document {document_id}. Run ingest first.")
    turn_doc_context_chars = _safe_int(tt_opts.get("doc_context_chars"), 1800, min_value=500, max_value=12000)
    outline_context_chars = _safe_int(tt_opts.get("outline_context_chars"), max(turn_doc_context_chars, 2200), min_value=600, max_value=16000)
    context = _compose_script_context(chunks, max_chars=turn_doc_context_chars, max_chunks=ctx_top_k, per_chunk_chars=1000)
    outline_context = _compose_script_context(chunks, max_chars=outline_context_chars, max_chunks=ctx_top_k, per_chunk_chars=1200)
    scenario_prompt_ru = str(scenario_meta.get("prompt_ru", "") or "")
    scenario_extra_ru, _scenario_extra_en = _scenario_extra_guidance(scenario_key, scenario_options, voice_list)

    sequence = _turn_taking_role_sequence(scenario_key, voice_list, minutes, scenario_options)
    use_outline = _safe_bool(tt_opts.get("use_outline"), True)
    outline_summary: str | None = None
    turn_plan: list[dict[str, Any]] = [{"role": role} for role in sequence]
    if use_outline:
        try:
            if outline_plan is not None:
                outline = _normalize_turn_outline(
                    outline_plan,
                    voices=voice_list,
                    total_turns=len(sequence),
                    scenario_key=scenario_key,
                )
            else:
                outline = await _generate_turn_taking_outline(
                    context=outline_context,
                    style_name=style_name,
                    style_hint=style_hint,
                    scenario_key=scenario_key,
                    scenario_label=scenario_label,
                    scenario_prompt_ru=scenario_prompt_ru,
                    scenario_extra_ru=scenario_extra_ru,
                    voices=voice_list,
                    minutes=minutes,
                    target_turns=len(sequence),
                    role_llm_map=role_llm_map,
                    context_char_budget=outline_context_chars,
                    effective_knowledge_mode=effective_knowledge_mode,
                )
            outline_summary = _format_turn_outline_summary(outline, max_chars=900)
            turn_plan = _expand_turn_outline_to_plan(outline, voices=voice_list, fallback_sequence=sequence)
        except Exception as e:
            logger.warning("Turn-taking outline generation failed; fallback to direct sequence: %s", e)
            outline = {
                "scenario": scenario_key,
                "episode_goal": "",
                "blocks": _outline_default_blocks(scenario_key, voice_list, len(sequence)),
            }
            outline_summary = _format_turn_outline_summary(outline, max_chars=900)
            turn_plan = _expand_turn_outline_to_plan(outline, voices=voice_list, fallback_sequence=sequence)

    total_words = max(120, minutes * 150)
    words_per_turn = max(12, int(total_words / max(1, len(turn_plan))))
    history_max_lines = _safe_int(tt_opts.get("history_window_lines"), 8, min_value=3, max_value=20)
    history_recent_char_budget = _safe_int(tt_opts.get("history_window_chars"), 1200, min_value=400, max_value=8000)
    history_summary_char_budget = _safe_int(tt_opts.get("history_summary_chars"), 350, min_value=0, max_value=3000)
    turn_response_retries = _safe_int(tt_opts.get("turn_response_retries"), 2, min_value=0, max_value=4)
    history: list[DialogueLine] = []

    for idx, step in enumerate(turn_plan):
        role = str(step.get("role") or voice_list[0]).strip() or voice_list[0]
        system, user = _turn_taking_prompts(
            context=context,
            style_name=style_name,
            style_hint=style_hint,
            scenario_label=scenario_label,
            scenario_prompt_ru=scenario_prompt_ru,
            scenario_extra_ru=scenario_extra_ru,
            voice_list=voice_list,
            role=role,
            history=history,
            turn_index=idx,
            total_turns=len(turn_plan),
            target_words=words_per_turn,
            history_max_lines=history_max_lines,
            history_recent_char_budget=history_recent_char_budget,
            history_summary_char_budget=history_summary_char_budget,
            outline_summary=outline_summary,
            current_block=step if step.get("block_title") else None,
            effective_knowledge_mode=effective_knowledge_mode,
        )
        role_override = _extract_role_llm_override(role, role_llm_map)
        attempt_prompt_budgets = [
            (context, history_max_lines, history_recent_char_budget, history_summary_char_budget, outline_summary),
            (_truncate_prompt_text(context, max(700, len(context) // 2)), max(4, min(history_max_lines, 6)), min(history_recent_char_budget, 700), min(history_summary_char_budget, 180), _truncate_prompt_text(outline_summary or "", 450) or None),
            (_truncate_prompt_text(context, 900), 3, 450, 120, _truncate_prompt_text(outline_summary or "", 250) or None),
        ]
        raw = None
        text = ""
        last_exc: Exception | None = None
        for attempt_i, (ctx_attempt, h_lines, h_recent, h_summary, outline_attempt) in enumerate(attempt_prompt_budgets):
            try:
                if attempt_i > 0:
                    system, user = _turn_taking_prompts(
                        context=ctx_attempt,
                        style_name=style_name,
                        style_hint=style_hint,
                        scenario_label=scenario_label,
                        scenario_prompt_ru=scenario_prompt_ru,
                        scenario_extra_ru=scenario_extra_ru,
                        voice_list=voice_list,
                        role=role,
                        history=history,
                        turn_index=idx,
                        total_turns=len(turn_plan),
                        target_words=words_per_turn,
                        history_max_lines=h_lines,
                        history_recent_char_budget=h_recent,
                        history_summary_char_budget=h_summary,
                        outline_summary=outline_attempt,
                        current_block=step if step.get("block_title") else None,
                        effective_knowledge_mode=effective_knowledge_mode,
                    )
                retry_note = ""
                raw_candidate = None
                text_candidate = ""
                for resp_retry_i in range(max(1, turn_response_retries + 1)):
                    user_attempt = user
                    if retry_note:
                        user_attempt = (
                            f"{user}\n\n"
                            "ВАЖНО: предыдущий ответ был некорректным/оборванным. "
                            f"{retry_note} Верни одну цельную завершённую реплику без имени роли."
                        )
                    raw_candidate = await llm_service.chat_completion(
                        system,
                        user_attempt,
                        temperature=0.6,
                        max_tokens=700,
                        model=(role_override or {}).get("model"),
                        base_url=(role_override or {}).get("base_url"),
                    )
                    text_candidate = _clean_turn_text(str(raw_candidate or ""), role)
                    quality_issue = _analyze_turn_text_quality(text_candidate, target_words=words_per_turn)
                    if quality_issue is None:
                        break
                    if resp_retry_i >= turn_response_retries:
                        break
                    if quality_issue == "empty":
                        retry_note = "Ответ оказался пустым."
                    elif quality_issue == "too_short":
                        retry_note = "Ответ слишком короткий, раскрой мысль хотя бы в одной-двух полных фразах."
                    else:
                        retry_note = "Ответ выглядит оборванным; закончи мысль полностью и поставь завершающую пунктуацию."
                    logger.warning(
                        "Retrying bad turn output for role %s turn %s (issue=%s, retry=%s/%s)",
                        role,
                        idx + 1,
                        quality_issue,
                        resp_retry_i + 1,
                        turn_response_retries,
                    )
                raw = str(raw_candidate or "")
                text = _clean_turn_text(raw, role)
                break
            except llm_service.LMStudioError as e:
                last_exc = e
                if not _is_context_overflow_error(e) or attempt_i == len(attempt_prompt_budgets) - 1:
                    raise
                logger.warning(
                    "Turn-taking prompt overflow for role %s turn %s; retry %s with smaller prompt",
                    role,
                    idx + 1,
                    attempt_i + 2,
                )
        if raw is None and last_exc is not None:
            raise last_exc
        raw = str(raw or "")
        text = _clean_turn_text(raw, role)
        quality_issue = _analyze_turn_text_quality(text, target_words=words_per_turn)
        if quality_issue == "empty":
            logger.warning("Empty turn-taking line for role %s at turn %s; using fallback line", role, idx + 1)
            text = "Продолжим разбор документа по текущему тезису."
        elif quality_issue == "too_short":
            logger.warning("Too short turn-taking line for role %s at turn %s; extending fallback", role, idx + 1)
            text = f"{text} Продолжим разбор документа по текущему тезису." if text else "Продолжим разбор документа по текущему тезису."
        elif quality_issue == "truncated":
            logger.warning("Truncated-looking turn-taking line for role %s at turn %s; patching ending", role, idx + 1)
            text = f"{text} Продолжим мысль на следующей реплике."
        text = _finalize_turn_text(text)
        line = DialogueLine(voice=role, text=text, grounding=_line_grounding(text, effective_knowledge_mode=effective_knowledge_mode))
        history.append(line)
        yield line


async def generate_podcast_script(
    document_id: str,
    minutes: int = 5,
    style: str = "conversational",
    focus: str | None = None,
    voices: list[str] | None = None,
    scenario: str = "classic_overview",
    scenario_options: dict[str, Any] | None = None,
    generation_mode: str = "single_pass",
    role_llm_map: dict[str, Any] | None = None,
    outline_plan: dict[str, Any] | None = None,
    tts_friendly: bool = True,
    knowledge_mode: str | None = None,
) -> list[DialogueLine]:
    """Generate a multi-voice podcast script from the document content."""
    mode = _normalize_generation_mode(generation_mode)
    voice_list_input = voices or ["host", "guest1", "guest2"]
    if mode == "turn_taking":
        lines = [
            line
            async for line in iter_podcast_script_turn_taking(
                document_id,
                minutes=minutes,
                style=style,
                focus=focus,
                voices=voice_list_input,
                scenario=scenario,
                scenario_options=scenario_options,
                role_llm_map=role_llm_map,
                outline_plan=outline_plan,
                knowledge_mode=knowledge_mode,
            )
        ]
        validate_script_completeness(lines, voice_list_input, minutes=minutes, mode=mode)
        if tts_friendly:
            lines = await rewrite_script_tts_second_pass(lines, voice_list_input)
            for line in lines:
                line.text = latin_to_russian_readable_keep_pauses(line.text)
        return lines

    effective_knowledge_mode = _effective_script_knowledge_mode(scenario, knowledge_mode)
    system, user, voice_list = _script_prompts(
        document_id,
        minutes=minutes,
        style=style,
        focus=focus,
        voices=voice_list_input,
        scenario=scenario,
        scenario_options=scenario_options,
        tts_friendly=tts_friendly,
        knowledge_mode=knowledge_mode,
    )
    validate_role_llm_map(voice_list, role_llm_map)
    _role_name, llm_override = await validate_primary_role_llm_preflight(voice_list, role_llm_map)
    raw = await llm_service.chat_completion(
        system,
        user,
        temperature=0.6,
        max_tokens=8192,
        model=(llm_override or {}).get("model"),
        base_url=(llm_override or {}).get("base_url"),
    )
    lines = _parse_script_json(raw, voice_list)
    for line in lines:
        line.grounding = _line_grounding(line.text, effective_knowledge_mode=effective_knowledge_mode)
    validate_script_completeness(lines, voice_list, minutes=minutes, mode=mode)
    if tts_friendly:
        lines = await rewrite_script_tts_second_pass(lines, voice_list)
        for line in lines:
            line.text = latin_to_russian_readable_keep_pauses(line.text)
    return lines


async def rewrite_script_tts_second_pass(
    lines: list[DialogueLine],
    voices: list[str],
) -> list[DialogueLine]:
    """Second LLM pass: rewrite already generated script into strict TTS-friendly Russian text."""
    if not lines:
        return lines

    payload = [{"voice": l.voice, "text": l.text} for l in lines]
    system = (
        "Ты редактор текста для синтеза речи.\n"
        "Твоя задача: переписать каждую реплику так, чтобы её было естественно озвучивать на русском TTS.\n"
        "Правила (строго):\n"
        "1) Только русский текст, никаких латинских букв.\n"
        "2) Числа, годы, даты, проценты и диапазоны — только словами.\n"
        "3) Английские слова типа telegram, youtube, podcast — русской транскрипцией (телеграмм, ютуб, подкаст).\n"
        "4) Аббревиатуры (SQL, API, GPT и т.п.) — чтение по буквам через дефис (эс-кью-эл, эй-пи-ай, джи-пи-ти).\n"
        "5) Не используй технические маркеры и спец-теги (например [PAUSE_*], SSML и квадратные скобки).\n"
        "6) Сохрани смысл, порядок реплик и количество реплик.\n"
        "7) Не добавляй и не удаляй говорящих; поле voice оставь без изменений.\n\n"
        "Вывод: ТОЛЬКО JSON-массив объектов {\"voice\":\"...\",\"text\":\"...\"} без комментариев."
    )
    user = "Входной скрипт:\n" + json.dumps(payload, ensure_ascii=False)

    try:
        raw = await llm_service.chat_completion(
            system,
            user,
            temperature=0.0,
            max_tokens=8192,
            timeout_seconds=TTS_REWRITE_TIMEOUT_SECONDS,
        )
        rewritten = _parse_script_json(raw, voices)
        if len(rewritten) != len(lines):
            logger.warning(
                "TTS second pass returned %s lines, expected %s; fallback to original lines",
                len(rewritten), len(lines),
            )
            return lines
        safe: list[DialogueLine] = []
        for original, candidate in zip(lines, rewritten):
            text = (candidate.text or "").strip()
            safe.append(DialogueLine(voice=original.voice, text=text or original.text, grounding=original.grounding))
        return safe
    except Exception as e:
        logger.warning("TTS second pass failed, fallback to original lines: %s", e)
        return lines


def validate_script_completeness(
    lines: list[DialogueLine],
    voices: list[str],
    *,
    minutes: int,
    mode: str = "single_pass",
) -> None:
    """Reject obviously truncated/degenerate outputs (e.g. one short phrase)."""
    if not lines:
        raise ValueError("LLM вернула пустой скрипт.")

    nonempty = [ln for ln in lines if str(getattr(ln, "text", "") or "").strip()]
    if not nonempty:
        raise ValueError("LLM вернула пустые реплики без текста.")

    total_words = sum(len(re.findall(r"\w+", str(ln.text or ""), flags=re.UNICODE)) for ln in nonempty)
    total_chars = sum(len(str(ln.text or "").strip()) for ln in nonempty)
    multi_voice = len([v for v in voices if str(v).strip()]) > 1

    if multi_voice and len(nonempty) < 2:
        raise ValueError(
            "LLM вернула слишком короткий скрипт (только одна реплика для многоголосого сценария). "
            "Повторите генерацию или уменьшите длину/сложность сценария."
        )

    # Lenient floor to catch obvious truncation while avoiding false positives.
    min_words = max(18, minutes * 35)
    min_chars = max(80, minutes * 140)
    if total_words < min_words or total_chars < min_chars:
        raise ValueError(
            "LLM вернула слишком короткий скрипт (похоже, ответ оборвался или модель не выполнила формат). "
            "Попробуйте повторить генерацию; при повторении можно переключить модель или режим turn-taking."
        )

    if mode == "turn_taking":
        distinct_roles = {str(ln.voice or "").strip() for ln in nonempty if str(ln.voice or "").strip()}
        if multi_voice and len(distinct_roles) < 2:
            raise ValueError(
                "Пошаговая генерация вернула реплики только одного спикера. "
                "Попробуйте повторить генерацию или проверьте роли/сценарий."
            )


_RU_TO_LAT_MAP = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z",
    "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def _voice_match_key(text: Any) -> str:
    s = str(text or "").strip().lower()
    return re.sub(r"[^a-zа-яё0-9]+", "", s)


def _voice_match_key_translit(text: Any) -> str:
    s = str(text or "").strip().lower()
    out: list[str] = []
    for ch in s:
        if ch in _RU_TO_LAT_MAP:
            out.append(_RU_TO_LAT_MAP[ch])
        elif ch.isascii() and (ch.isalnum() or ch in {"_", "-", " "}):
            out.append(ch)
        elif ch.isdigit():
            out.append(ch)
    return re.sub(r"[^a-z0-9]+", "", "".join(out))


def _normalize_voice_label_to_allowed(raw_voice: Any, voices: list[str]) -> str:
    """Map model-emitted voice labels (host/guest1/igor) to configured voices."""
    fallback = voices[0] if voices else "host"
    raw = str(raw_voice or "").strip()
    if not raw or not voices:
        return fallback

    # Exact / case-insensitive direct match
    for v in voices:
        if raw == v or raw.lower() == str(v).lower():
            return v

    direct_map: dict[str, str] = {}
    translit_map: dict[str, str] = {}
    for v in voices:
        direct_map.setdefault(_voice_match_key(v), v)
        translit_key = _voice_match_key_translit(v)
        if translit_key:
            translit_map.setdefault(translit_key, v)

    raw_key = _voice_match_key(raw)
    if raw_key in direct_map:
        return direct_map[raw_key]

    raw_key_translit = _voice_match_key_translit(raw)
    if raw_key_translit and raw_key_translit in translit_map:
        return translit_map[raw_key_translit]

    role_key = raw_key or raw_key_translit
    role_aliases = {
        "host": 0,
        "moderator": 0,
        "veduschiy": 0,
        "vedushiy": 0,
        "ведущий": 0,
        "модератор": 0,
        "teacher": 0,
        "учитель": 0,
        "guest": 1,
        "guest1": 1,
        "guesta": 1,
        "speakera": 1,
        "speaker1": 1,
        "ученик": 1,
        "student": 1,
        "guest2": 2,
        "guestb": 2,
        "speakerb": 2,
        "speaker2": 2,
        "guest3": 3,
        "speaker3": 3,
        "guest4": 4,
        "speaker4": 4,
    }
    if role_key in role_aliases:
        idx = role_aliases[role_key]
        if idx < len(voices):
            return voices[idx]

    m = re.match(r"^(?:guest|speaker)(\d+)$", role_key or "")
    if m:
        idx = max(0, int(m.group(1)) - 1)
        if idx < len(voices):
            return voices[idx]

    return fallback


def _parse_script_json(raw: str, voices: list[str]) -> list[DialogueLine]:
    """Robustly extract dialogue JSON from LLM output."""
    def _normalize_item(item: dict[str, Any]) -> DialogueLine | None:
        if not isinstance(item, dict):
            return None
        voice_val = None
        text_val = None
        for k, v in item.items():
            key = str(k or "").strip().lower()
            if key in {"voice", "voices", "speaker", "role", "голос", "спикер", "роль", "войс", "войсе", "войке"}:
                voice_val = v
            elif key in {"text", "текст", "реплика", "фраза"}:
                text_val = v
        text = str(text_val or "").strip()
        if not text:
            return None
        voice = _normalize_voice_label_to_allowed(voice_val or voices[0], voices)
        grounding = str(item.get("grounding") or "").strip() or None
        return DialogueLine(voice=voice, text=text, grounding=grounding)

    def _parse_array_blob(blob: str) -> list[DialogueLine] | None:
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, list):
            return None
        out = []
        for item in data:
            norm = _normalize_item(item)
            if norm is not None:
                out.append(norm)
        return out

    def _repair_jsonish_blob(blob: str) -> str:
        s = blob.strip()
        s = s.replace("“", '"').replace("”", '"').replace("„", '"').replace("«", '"').replace("»", '"')
        s = s.replace("’", "'").replace("`", "'")
        s = re.sub(
            r'"(?:voice|voices|speaker|role|голос|спикер|роль|войс|войсе|войке)"\s*:',
            '"voice":',
            s,
            flags=re.IGNORECASE,
        )
        s = re.sub(
            r'"(?:text|текст|реплика|фраза)"\s*:',
            '"text":',
            s,
            flags=re.IGNORECASE,
        )
        if "[" not in s and "{" in s and "}" in s:
            objs = re.findall(r"\{[^{}]*\}", s, flags=re.DOTALL)
            if objs:
                s = "[" + ",".join(objs) + "]"
        return s

    def _looks_like_jsonish_script(s: str) -> bool:
        low = (s or "").lower()
        return (
            ("{" in s or "[" in s)
            and any(
                token in low
                for token in (
                    '"voice"',
                    '"text"',
                    '"голос"',
                    '"текст"',
                    '"войс"',
                    '"войке"',
                    "json",
                    "джсон",
                )
            )
        )

    # Try to find JSON array in the output
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if match:
        blob = match.group()
        parsed = _parse_array_blob(blob)
        if parsed:
            return parsed
        repaired = _repair_jsonish_blob(blob)
        if repaired != blob:
            parsed = _parse_array_blob(repaired)
            if parsed:
                return parsed

    # Try object-sequence repair even without array brackets
    if _looks_like_jsonish_script(raw):
        repaired = _repair_jsonish_blob(raw)
        parsed = _parse_array_blob(repaired)
        if parsed:
            return parsed

    # Fallback: split by voice labels
    lines: list[DialogueLine] = []
    for voice in voices:
        pattern = rf'{voice}\s*:\s*(.+?)(?=(?:{"|".join(voices)})\s*:|$)'
        for m in re.finditer(pattern, raw, re.DOTALL | re.IGNORECASE):
            lines.append(DialogueLine(voice=voice, text=m.group(1).strip()))

    if not lines:
        if _looks_like_jsonish_script(raw):
            raise ValueError("LLM вернула повреждённый JSON скрипта (не удалось восстановить ключи/структуру).")
        # Last resort: treat entire output as host monologue
        lines = [DialogueLine(voice=voices[0], text=raw.strip())]

    return lines


def _qa_context(
    document_ids: list[str],
    query: str,
    top_k_per_doc: int = 6,
    max_chunks: int = 8,
    fallback_chunks: int = 3,
    require_terms: bool = False,
    prefer_math_chunks: bool = False,
) -> list[dict]:
    items: list[dict] = []
    question_terms = _extract_terms(query)
    for document_id in document_ids:
        rows = rag_service.retrieve(document_id, query, top_k=top_k_per_doc)
        for r in rows:
            text = r.get("text", "") or ""
            score = float(r.get("score", 0.0) or 0.0)
            meta = r.get("meta")
            page = None
            if isinstance(meta, dict):
                page = meta.get("page") or meta.get("page_number") or meta.get("page_index")
            if page is None:
                page = r.get("page")
            item = {
                "document_id": document_id,
                "chunk_id": r.get("chunk_id", ""),
                "chunk_index": r.get("chunk_index"),
                "text": text,
                "score": score,
                "section_path": r.get("section_path"),
                "page": page,
                "anchor": r.get("anchor"),
                "caption": r.get("caption"),
                "source_type": r.get("source_type"),
                "source_locator": r.get("source_locator")
                or rag_service.build_source_locator(
                    chunk_id=str(r.get("chunk_id", "")),
                    chunk_index=r.get("chunk_index"),
                    text=text,
                    page=page,
                    section_path=r.get("section_path"),
                    anchor=r.get("anchor"),
                    caption=r.get("caption"),
                    source_type=r.get("source_type"),
                ),
            }
            low = text.lower()
            item["matched_terms"] = sorted({t for t in question_terms if t in low})
            item["highlights"] = _extract_highlights(text, question_terms, limit=3)
            item["has_math"] = _snippet_has_math(text)
            items.append(item)

    if not items:
        return []
    items.sort(key=lambda x: float(x.get("score", 0.0) or 0.0), reverse=True)
    top_score = float(items[0].get("score", 0.0) or 0.0)
    threshold = max(0.05, top_score * 0.45) if top_score > 0 else 0.0
    chosen: list[dict] = []
    deferred_terms: list[dict] = []
    deferred_math: list[dict] = []
    seen_texts: set[str] = set()

    for item in items:
        if len(chosen) >= max_chunks:
            break
        text_norm = re.sub(r"\s+", " ", (item.get("text") or "")[:220]).lower().strip()
        if not text_norm or text_norm in seen_texts:
            continue
        seen_texts.add(text_norm)
        score = float(item.get("score", 0.0) or 0.0)
        has_terms = bool(item.get("matched_terms")) or not question_terms
        if score < threshold and chosen:
            continue
        if require_terms and not has_terms:
            deferred_terms.append(item)
            continue
        if prefer_math_chunks and not bool(item.get("has_math")):
            deferred_math.append(item)
            continue
        chosen.append(item)

    if len(chosen) < max_chunks:
        for pool in (deferred_terms, deferred_math):
            if len(chosen) >= max_chunks:
                break
            for item in pool:
                chosen.append(item)
                if len(chosen) >= max_chunks:
                    break

    if not chosen:
        return items[:fallback_chunks]
    return chosen[:max_chunks]


def _qa_summary_context(
    document_ids: list[str],
    *,
    per_doc_chars: int = 500,
    total_chars: int = 1400,
) -> str:
    blocks: list[str] = []
    total = 0
    for document_id in document_ids:
        doc = document_store.get_document(document_id) or {}
        summary = re.sub(r"\s+", " ", str(doc.get("summary") or "")).strip()
        if not summary:
            continue
        title = str(doc.get("filename") or document_id).strip() or document_id
        clipped = _truncate_prompt_text(summary, per_doc_chars)
        block = f"[{title}]\n{clipped}"
        block_len = len(block) + (2 if blocks else 0)
        if total > 0 and (total + block_len) > total_chars:
            break
        blocks.append(block)
        total += block_len
    return "\n\n".join(blocks)


def _history_slice(history: list[dict] | None, max_messages: int = 8) -> list[dict]:
    rows = history or []
    cleaned: list[dict] = []
    for item in rows[-max_messages:]:
        role = str(item.get("role", "")).strip().lower()
        if role not in {"user", "assistant"}:
            continue
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        if len(text) > 900:
            text = text[:900] + "..."
        cleaned.append({"role": role, "text": text})
    return cleaned


def _conversation_query(question: str, history: list[dict] | None) -> str:
    hist = _history_slice(history, max_messages=6)
    user_q = [x["text"] for x in hist if x["role"] == "user"][-2:]
    parts = [*user_q, question]
    return " | ".join([p.strip() for p in parts if p.strip()])


def _safe_id_token(value: Any) -> str:
    token = re.sub(r"[^a-zA-Z0-9._:-]+", "_", str(value or "").strip())
    return token or "na"


def _build_evidence_id(item: dict[str, Any]) -> str:
    doc = _safe_id_token(item.get("document_id"))
    chunk = _safe_id_token(item.get("chunk_id"))
    idx = item.get("chunk_index")
    idx_token = str(idx) if isinstance(idx, int) and idx >= 0 else "na"
    return f"ev:{doc}:{chunk}:{idx_token}"


def _build_anchor_id(item: dict[str, Any]) -> str:
    doc = _safe_id_token(item.get("document_id"))
    chunk = _safe_id_token(item.get("chunk_id"))
    locator = item.get("source_locator") if isinstance(item.get("source_locator"), dict) else {}
    page = locator.get("page") if locator else None
    if page is None:
        page = item.get("page")
    slide = locator.get("slide") if locator else None
    start = locator.get("char_start") if locator else None
    end = locator.get("char_end") if locator else None

    if isinstance(start, int) and start >= 0:
        length = max(1, int(end) - start) if isinstance(end, int) else 1
        return f"a:{doc}:{chunk}:o{start}:{length}"
    if isinstance(page, int):
        return f"a:{doc}:{chunk}:p{page}"
    if isinstance(slide, int):
        return f"a:{doc}:{chunk}:s{slide}"

    anchor_seed = str(
        item.get("anchor")
        or item.get("section_path")
        or item.get("caption")
        or item.get("text")
        or chunk
    )
    digest = hashlib.sha1(anchor_seed.encode("utf-8", errors="ignore")).hexdigest()[:10]
    return f"a:{doc}:{chunk}:h{digest}"


def _build_citation_payload(item: dict[str, Any]) -> dict[str, Any]:
    text = str(item.get("text") or "")
    return {
        "evidence_id": _build_evidence_id(item),
        "anchor_id": _build_anchor_id(item),
        "document_id": item["document_id"],
        "chunk_id": item["chunk_id"],
        "chunk_index": item.get("chunk_index"),
        "section_path": item.get("section_path"),
        "page": item.get("page"),
        "anchor": item.get("anchor"),
        "caption": item.get("caption"),
        "source_type": item.get("source_type"),
        "source_locator": item.get("source_locator"),
        "score": round(float(item.get("score", 0.0) or 0.0), 4),
        "text": text[:220] + ("..." if len(text) > 220 else ""),
        "highlights": item.get("highlights") or [],
        "matched_terms": item.get("matched_terms") or [],
    }


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _confidence_breakdown(ctx: list[dict], *, cap: int = 8) -> dict[str, float]:
    rows = list(ctx or [])[: max(1, int(cap))]
    if not rows:
        return {
            "retrieval_quality": 0.0,
            "evidence_coverage": 0.0,
            "answer_grounding": 0.0,
        }
    retrieval_quality = _clamp01(mean([float(c.get("score", 0.0) or 0.0) for c in rows]))
    evidence_coverage = _clamp01(min(len(rows), cap) / float(cap))
    grounded = 0
    for item in rows:
        terms = item.get("matched_terms") if isinstance(item.get("matched_terms"), list) else []
        locator = item.get("source_locator") if isinstance(item.get("source_locator"), dict) else {}
        if terms:
            grounded += 1
            continue
        if isinstance(locator.get("char_start"), int) and isinstance(locator.get("char_end"), int):
            grounded += 1
            continue
        quote = str(locator.get("quote") or "").strip()
        text = str(item.get("text") or "")
        if quote and quote.lower() in text.lower():
            grounded += 1
    answer_grounding = _clamp01(grounded / max(1, len(rows)))
    return {
        "retrieval_quality": round(retrieval_quality, 3),
        "evidence_coverage": round(evidence_coverage, 3),
        "answer_grounding": round(answer_grounding, 3),
    }


def build_qa_payload(
    document_ids: list[str],
    question: str,
    strict_sources: bool = False,
    use_summary_context: bool = False,
    question_mode: str | None = None,
    answer_length: str | None = None,
    knowledge_mode: str | None = None,
) -> tuple[str, str, list[dict], float, dict[str, float], str]:
    q = (question or "").strip()
    if not q:
        raise ValueError("Вопрос не должен быть пустым")
    doc_ids = [str(x).strip() for x in document_ids if str(x).strip()]
    if not doc_ids:
        raise ValueError("Нужен хотя бы один document_id")

    _mode_key, mode_cfg = _resolve_question_mode(question_mode)
    _len_key, len_cfg = resolve_answer_length(answer_length)
    effective_knowledge_mode = _normalize_knowledge_mode(knowledge_mode)
    ctx = _qa_context(
        doc_ids,
        q,
        top_k_per_doc=6,
        max_chunks=int(mode_cfg.get("max_chunks", 8)),
        fallback_chunks=int(mode_cfg.get("fallback_chunks", 3)),
        require_terms=bool(mode_cfg.get("require_terms", False)),
        prefer_math_chunks=bool(mode_cfg.get("prefer_math_chunks", False)),
    )
    if not ctx:
        raise ValueError("Нет проиндексированных данных по выбранным документам. Запустите индексацию.")
    summary_context = _qa_summary_context(doc_ids) if use_summary_context else ""

    joined = "\n\n---\n\n".join(
        f"[doc={c['document_id']} chunk={c['chunk_id']} score={c['score']:.3f}]\n{c['text']}"
        for c in ctx
    )
    if strict_sources:
        top = float(ctx[0].get("score", 0.0) or 0.0)
        if len(ctx) < 2 or top < 0.45:
            raise ValueError("В строгом режиме нет достаточно релевантных источников. Уточните вопрос или переиндексируйте документ.")
        system = (
            "Ты ассистент по документам. Отвечай только на русском. "
            "СТРОГИЙ РЕЖИМ: используй только факты, напрямую подтвержденные контекстом; "
            "если в контексте нет точного ответа, прямо ответь 'Недостаточно данных в источниках'. "
            "Не добавляй предположений и внешних знаний. "
            "В конце ответа добавь блок 'Источники:' со списком [doc/chunk]."
        )
        if summary_context:
            system += " Блок 'Краткие саммари' используй только как вспомогательный ориентир, а не как источник фактов."
    else:
        if effective_knowledge_mode == "hybrid_model":
            system = (
                "Ты ассистент по документам. Отвечай только на русском. "
                "Используй документ как основную опору. Разрешено добавлять внешние профессиональные знания, "
                "критику, альтернативы и предложения. Любую мысль, которая не подтверждена документом, "
                "помечай в начале абзаца префиксом 'Вне документа:'. Также допустимы префиксы 'Гипотеза модели:', "
                "'Предложение модели:' и 'Критика модели:'. Не выдумывай citations и не приписывай внешние идеи документу. "
                "Если по документу данных недостаточно, сначала скажи это, а затем при необходимости дай отдельный блок с внешними соображениями. "
                "В конце ответа добавь короткий блок 'Источники:' только для частей, подтвержденных контекстом, со списком [doc/chunk]."
            )
        else:
            system = (
                "Ты ассистент по документам. Отвечай только на русском. "
                "Опирайся только на предоставленный контекст. "
                "Если данных недостаточно, так и скажи. "
                "В конце ответа добавь короткий блок 'Источники:' со списком [doc/chunk]."
            )
        if summary_context:
            system += " Блок 'Краткие саммари' используй как вспомогательный контекст для навигации."
    mode_instruction = str(mode_cfg.get("instruction", "")).strip()
    if mode_instruction:
        system += f" {mode_instruction}"
    length_instruction = str(len_cfg.get("instruction", "")).strip()
    if length_instruction:
        system += f" {length_instruction}"
    summary_block = f"Краткие саммари (вспомогательно):\n{summary_context}\n\n" if summary_context else ""
    user = f"Вопрос:\n{q}\n\n{summary_block}Контекст:\n{joined}"
    conf_breakdown = _confidence_breakdown(ctx, cap=8)
    confidence = round(
        _clamp01((conf_breakdown["retrieval_quality"] * 0.75) + (conf_breakdown["evidence_coverage"] * 0.25)),
        3,
    )
    citations = [_build_citation_payload(c) for c in ctx[:8]]
    return system, user, citations, confidence, conf_breakdown, effective_knowledge_mode


async def answer_question(
    document_ids: list[str],
    question: str,
    strict_sources: bool = False,
    use_summary_context: bool = False,
    question_mode: str | None = None,
    answer_length: str | None = None,
    knowledge_mode: str | None = None,
) -> dict:
    system, user, citations, confidence, confidence_breakdown, effective_knowledge_mode = build_qa_payload(
        document_ids,
        question,
        strict_sources=strict_sources,
        use_summary_context=use_summary_context,
        question_mode=question_mode,
        answer_length=answer_length,
        knowledge_mode=knowledge_mode,
    )
    answer_len_key, answer_len_cfg = resolve_answer_length(answer_length)
    answer = await llm_service.chat_completion(
        system,
        user,
        temperature=0.2,
        max_tokens=int(answer_len_cfg.get("max_tokens", 2200)),
    )
    return {
        "answer": answer,
        "confidence": confidence,
        "confidence_breakdown": confidence_breakdown,
        "citations": citations,
        "mode": _resolve_question_mode(question_mode)[0],
        "answer_length": answer_len_key,
        "knowledge_mode": _normalize_knowledge_mode(knowledge_mode),
        "effective_knowledge_mode": effective_knowledge_mode,
        "has_model_knowledge_content": _answer_has_model_knowledge_content(answer),
    }


def build_conversational_qa_payload(
    document_ids: list[str],
    question: str,
    history: list[dict] | None,
    strict_sources: bool = False,
    use_summary_context: bool = False,
    question_mode: str | None = None,
    answer_length: str | None = None,
    knowledge_mode: str | None = None,
) -> tuple[str, str, list[dict], float, dict[str, float], str]:
    q = (question or "").strip()
    if not q:
        raise ValueError("Вопрос не должен быть пустым")
    doc_ids = [str(x).strip() for x in document_ids if str(x).strip()]
    if not doc_ids:
        raise ValueError("Нужен хотя бы один document_id")

    conv_query = _conversation_query(q, history)
    _mode_key, mode_cfg = _resolve_question_mode(question_mode)
    _len_key, len_cfg = resolve_answer_length(answer_length)
    effective_knowledge_mode = _normalize_knowledge_mode(knowledge_mode)
    ctx = _qa_context(
        doc_ids,
        conv_query,
        top_k_per_doc=6,
        max_chunks=int(mode_cfg.get("max_chunks", 8)),
        fallback_chunks=int(mode_cfg.get("fallback_chunks", 3)),
        require_terms=bool(mode_cfg.get("require_terms", False)),
        prefer_math_chunks=bool(mode_cfg.get("prefer_math_chunks", False)),
    )
    if not ctx:
        raise ValueError("Нет проиндексированных данных по выбранным документам. Запустите индексацию.")
    summary_context = _qa_summary_context(doc_ids) if use_summary_context else ""

    conv = _history_slice(history, max_messages=8)
    conv_text = "\n".join(f"{'Пользователь' if x['role']=='user' else 'Ассистент'}: {x['text']}" for x in conv)
    joined = "\n\n---\n\n".join(
        f"[doc={c['document_id']} chunk={c['chunk_id']} score={c['score']:.3f}]\n{c['text']}"
        for c in ctx
    )
    if strict_sources:
        top = float(ctx[0].get("score", 0.0) or 0.0)
        if len(ctx) < 2 or top < 0.45:
            raise ValueError("В строгом режиме нет достаточно релевантных источников. Уточните вопрос или переиндексируйте документ.")
        system = (
            "Ты ассистент по документам в режиме conversational RAG. Отвечай только на русском. "
            "СТРОГИЙ РЕЖИМ: историю учитывай только для интерпретации вопроса, "
            "но отвечай только по фактам из контекста; если фактов нет, скажи 'Недостаточно данных в источниках'. "
            "Не добавляй догадок и внешних знаний. "
            "В конце ответа добавь блок 'Источники:' со списком [doc/chunk]."
        )
        if summary_context:
            system += " Блок 'Краткие саммари' используй только как вспомогательный ориентир, а не как источник фактов."
    else:
        if effective_knowledge_mode == "hybrid_model":
            system = (
                "Ты ассистент по документам в режиме conversational RAG. Отвечай только на русском. "
                "Учитывай историю диалога для интерпретации вопроса и используй документ как основную опору. "
                "Разрешено добавлять внешние профессиональные знания, критику и предложения, но любую мысль вне документа помечай "
                "в начале абзаца префиксом 'Вне документа:'. Не смешивай внешние знания с утверждениями о содержании документа без маркировки. "
                "В конце ответа добавь блок 'Источники:' только для подтвержденных документом частей со списком [doc/chunk]."
            )
        else:
            system = (
                "Ты ассистент по документам в режиме conversational RAG. Отвечай только на русском. "
                "Учитывай историю диалога только для интерпретации текущего вопроса, "
                "но факты бери только из предоставленного контекста документов. "
                "Если данных недостаточно, явно скажи это. "
                "В конце ответа добавь короткий блок 'Источники:' со списком [doc/chunk]."
            )
        if summary_context:
            system += " Блок 'Краткие саммари' используй как вспомогательный контекст для навигации."
    mode_instruction = str(mode_cfg.get("instruction", "")).strip()
    if mode_instruction:
        system += f" {mode_instruction}"
    length_instruction = str(len_cfg.get("instruction", "")).strip()
    if length_instruction:
        system += f" {length_instruction}"
    summary_block = f"Краткие саммари (вспомогательно):\n{summary_context}\n\n" if summary_context else ""
    user = (
        f"История диалога (последние сообщения):\n{conv_text or 'нет'}\n\n"
        f"Текущий вопрос:\n{q}\n\n"
        f"{summary_block}Контекст документов:\n{joined}"
    )
    conf_breakdown = _confidence_breakdown(ctx, cap=8)
    history_bonus = 0.08 if conv else 0.0
    confidence = round(
        _clamp01((conf_breakdown["retrieval_quality"] * 0.75) + (conf_breakdown["evidence_coverage"] * 0.25) + history_bonus),
        3,
    )
    citations = [_build_citation_payload(c) for c in ctx[:8]]
    return system, user, citations, confidence, conf_breakdown, effective_knowledge_mode


async def answer_question_conversational(
    document_ids: list[str],
    question: str,
    history: list[dict] | None,
    strict_sources: bool = False,
    use_summary_context: bool = False,
    question_mode: str | None = None,
    answer_length: str | None = None,
    knowledge_mode: str | None = None,
) -> dict:
    system, user, citations, confidence, confidence_breakdown, effective_knowledge_mode = build_conversational_qa_payload(
        document_ids,
        question,
        history,
        strict_sources=strict_sources,
        use_summary_context=use_summary_context,
        question_mode=question_mode,
        answer_length=answer_length,
        knowledge_mode=knowledge_mode,
    )
    answer_len_key, answer_len_cfg = resolve_answer_length(answer_length)
    answer = await llm_service.chat_completion(
        system,
        user,
        temperature=0.2,
        max_tokens=int(answer_len_cfg.get("max_tokens", 2200)),
    )
    return {
        "answer": answer,
        "confidence": confidence,
        "confidence_breakdown": confidence_breakdown,
        "citations": citations,
        "mode": _resolve_question_mode(question_mode)[0],
        "answer_length": answer_len_key,
        "knowledge_mode": _normalize_knowledge_mode(knowledge_mode),
        "effective_knowledge_mode": effective_knowledge_mode,
        "has_model_knowledge_content": _answer_has_model_knowledge_content(answer),
    }


async def compare_documents(document_ids: list[str], focus: str = "") -> dict:
    doc_ids = [str(x).strip() for x in document_ids if str(x).strip()]
    if len(doc_ids) < 2:
        raise ValueError("Для сравнения нужно минимум 2 документа")
    query = (focus or "").strip() or "главные темы, выводы и противоречия"
    ctx = _qa_context(doc_ids, query, top_k_per_doc=5)
    if not ctx:
        raise ValueError("Нет проиндексированных данных для сравнения")
    joined = "\n\n---\n\n".join(
        f"[doc={c['document_id']} chunk={c['chunk_id']}]\n{c['text']}"
        for c in ctx
    )
    system = (
        "Сделай сравнение документов на русском языке. "
        "Структура: 1) что совпадает, 2) в чем различия, 3) что противоречит, 4) практический вывод. "
        "Используй краткие пункты."
    )
    user = f"Фокус сравнения: {query}\n\nМатериалы:\n{joined}"
    summary = await llm_service.chat_completion(system, user, temperature=0.25, max_tokens=2600)
    per_doc = {d: 0 for d in doc_ids}
    for c in ctx:
        per_doc[c["document_id"]] = per_doc.get(c["document_id"], 0) + 1
    return {
        "comparison": summary,
        "document_ids": doc_ids,
        "context_coverage": per_doc,
    }

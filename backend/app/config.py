"""Application configuration loaded from environment and config.yaml.

LM Studio settings can be changed at runtime via the /api/settings endpoint.
"""

from __future__ import annotations

import os
import json
import re
import uuid
from pathlib import Path

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent  # backend/
ROOT_DIR = BASE_DIR.parent  # open-notebooklm/
DATA_DIR = Path(os.getenv("DATA_DIR", str(ROOT_DIR / "data")))
INPUTS_DIR = DATA_DIR / "inputs"
INDEX_DIR = DATA_DIR / "index"
OUTPUTS_DIR = DATA_DIR / "outputs"

for d in (INPUTS_DIR, INDEX_DIR, OUTPUTS_DIR):
    d.mkdir(parents=True, exist_ok=True)

# --- YAML loader ----------------------------------------------------------
def _resolve_config_path() -> Path:
    if os.getenv("CONFIG_YAML"):
        return Path(os.getenv("CONFIG_YAML", "")).resolve()
    # Prefer data/config.yaml when running locally so vision_ingest and other runtime config are shared
    data_config = DATA_DIR / "config.yaml"
    if data_config.exists():
        return data_config
    return BASE_DIR / "config.yaml"


CONFIG_YAML_PATH = _resolve_config_path()


def _load_yaml() -> dict:
    if CONFIG_YAML_PATH.exists():
        with open(CONFIG_YAML_PATH) as f:
            return yaml.safe_load(f) or {}
    return {}


def _save_yaml(data: dict) -> None:
    CONFIG_YAML_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_YAML_PATH, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


_cfg = _load_yaml()

# --- LM Studio (mutable at runtime) --------------------------------------
_lmstudio_cfg = _cfg.get("lmstudio", {})

LMSTUDIO_BASE_URL: str = os.getenv(
    "LMSTUDIO_BASE_URL",
    _lmstudio_cfg.get("base_url", "http://localhost:1234/v1"),
)
LMSTUDIO_MODEL: str = os.getenv(
    "LMSTUDIO_MODEL",
    _lmstudio_cfg.get("model", "local-model"),
)
LMSTUDIO_TEMPERATURE: float = float(
    _lmstudio_cfg.get("temperature", 0.4)
)
LMSTUDIO_MAX_TOKENS: int = int(
    _lmstudio_cfg.get("max_tokens", 4096)
)


def get_lmstudio_settings() -> dict:
    """Return current LM Studio settings."""
    return {
        "base_url": LMSTUDIO_BASE_URL,
        "model": LMSTUDIO_MODEL,
        "temperature": LMSTUDIO_TEMPERATURE,
        "max_tokens": LMSTUDIO_MAX_TOKENS,
    }


def update_lmstudio_settings(
    base_url: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> dict:
    """Update LM Studio settings in memory and persist to config.yaml."""
    global LMSTUDIO_BASE_URL, LMSTUDIO_MODEL, LMSTUDIO_TEMPERATURE, LMSTUDIO_MAX_TOKENS

    if base_url is not None:
        LMSTUDIO_BASE_URL = base_url
    if model is not None:
        LMSTUDIO_MODEL = model
    if temperature is not None:
        LMSTUDIO_TEMPERATURE = temperature
    if max_tokens is not None:
        LMSTUDIO_MAX_TOKENS = max_tokens

    # Persist to config.yaml
    cfg = _load_yaml()
    cfg.setdefault("lmstudio", {})
    cfg["lmstudio"]["base_url"] = LMSTUDIO_BASE_URL
    cfg["lmstudio"]["model"] = LMSTUDIO_MODEL
    cfg["lmstudio"]["temperature"] = LMSTUDIO_TEMPERATURE
    cfg["lmstudio"]["max_tokens"] = LMSTUDIO_MAX_TOKENS
    _save_yaml(cfg)

    return get_lmstudio_settings()


# --- ChromaDB ------------------------------------------------------------
CHROMA_HOST: str = os.getenv("CHROMA_HOST", "localhost")
CHROMA_PORT: int = int(os.getenv("CHROMA_PORT", "8000"))

# --- Runtime mode / job lanes ---------------------------------------------
_runtime_cfg = _cfg.get("runtime", {})
LOCAL_ONLY: bool = str(os.getenv("LOCAL_ONLY", _runtime_cfg.get("local_only", "1"))).strip().lower() in {"1", "true", "yes", "on"}
ALLOW_REMOTE_URL_INGEST: bool = str(
    os.getenv("ALLOW_REMOTE_URL_INGEST", _runtime_cfg.get("allow_remote_url_ingest", "1"))
).strip().lower() in {"1", "true", "yes", "on"}
ALLOW_REMOTE_LLM_ENDPOINT: bool = str(
    os.getenv("ALLOW_REMOTE_LLM_ENDPOINT", _runtime_cfg.get("allow_remote_llm_endpoint", "1"))
).strip().lower() in {"1", "true", "yes", "on"}
JOB_MAX_PARALLEL_DEFAULT: int = int(os.getenv("JOB_MAX_PARALLEL_DEFAULT", _runtime_cfg.get("job_max_parallel_default", 2)))
JOB_MAX_PARALLEL_INGEST: int = int(os.getenv("JOB_MAX_PARALLEL_INGEST", _runtime_cfg.get("job_max_parallel_ingest", 1)))
JOB_MAX_PARALLEL_AUDIO: int = int(os.getenv("JOB_MAX_PARALLEL_AUDIO", _runtime_cfg.get("job_max_parallel_audio", 1)))
JOB_MAX_PARALLEL_BATCH: int = int(os.getenv("JOB_MAX_PARALLEL_BATCH", _runtime_cfg.get("job_max_parallel_batch", 1)))

# --- OCR (mutable at runtime) --------------------------------------------
_ocr_cfg = _cfg.get("ocr", {})
OCR_SETTINGS: dict[str, object] = {
    "enabled": bool(_ocr_cfg.get("enabled", True)),
    "mode": str(_ocr_cfg.get("mode", "fast")).strip().lower() or "fast",
    "lang": str(_ocr_cfg.get("lang", "rus+eng")).strip() or "rus+eng",
    "min_chars": int(_ocr_cfg.get("min_chars", 8)),
    "max_pdf_pages": int(_ocr_cfg.get("max_pdf_pages", 40)),
    "max_docx_images": int(_ocr_cfg.get("max_docx_images", 40)),
}
if OCR_SETTINGS["mode"] not in {"fast", "accurate"}:
    OCR_SETTINGS["mode"] = "fast"
OCR_SETTINGS["min_chars"] = max(1, min(120, int(OCR_SETTINGS["min_chars"])))
OCR_SETTINGS["max_pdf_pages"] = max(1, min(500, int(OCR_SETTINGS["max_pdf_pages"])))
OCR_SETTINGS["max_docx_images"] = max(1, min(500, int(OCR_SETTINGS["max_docx_images"])))

# --- Vision ingest (describe images with VLM; mutable at runtime) ----------
_vision_cfg = _cfg.get("vision_ingest", {})
_vision_base = str(_vision_cfg.get("base_url", "")).strip() or None
if not _vision_base and _lmstudio_cfg.get("base_url"):
    _b = str(_lmstudio_cfg.get("base_url", "")).strip().rstrip("/")
    _vision_base = _b.replace("/v1", "") if "/v1" in _b else _b
VISION_INGEST_SETTINGS: dict[str, object] = {
    "enabled": bool(_vision_cfg.get("enabled", False)),
    "base_url": _vision_base or "http://localhost:1234",
    "model": str(_vision_cfg.get("model", "")).strip() or str(_lmstudio_cfg.get("model", "local-model")),
    "timeout_seconds": max(5, min(300, int(_vision_cfg.get("timeout_seconds", 60)))),
    "max_images_per_document": max(1, min(100, int(_vision_cfg.get("max_images_per_document", 20)))),
}
VISION_INGEST_SETTINGS["timeout_seconds"] = max(5, min(300, int(VISION_INGEST_SETTINGS["timeout_seconds"])))
VISION_INGEST_SETTINGS["max_images_per_document"] = max(1, min(100, int(VISION_INGEST_SETTINGS["max_images_per_document"])))

# --- Embeddings -----------------------------------------------------------
EMBEDDING_MODEL: str = os.getenv(
    "EMBEDDING_MODEL", "all-MiniLM-L6-v2"
)

# --- RAG tunables ---------------------------------------------------------
CHUNK_MIN = 800
CHUNK_MAX = 1200
CHUNK_OVERLAP_MIN = 150
CHUNK_OVERLAP_MAX = 250
RETRIEVAL_TOP_K = 8

# --- TTS / Piper ----------------------------------------------------------
_raw_piper = os.getenv("PIPER_BINARY", _cfg.get("piper", {}).get("binary", "piper"))
# Resolve relative path (e.g. ./piper_bin/piper) relative to backend/
if _raw_piper.startswith("./") or _raw_piper.startswith("../") or (not os.path.isabs(_raw_piper) and "/" in _raw_piper):
    _resolved = (BASE_DIR / _raw_piper).resolve()
    PIPER_BINARY = str(_resolved) if _resolved.exists() else _raw_piper
else:
    PIPER_BINARY = _raw_piper
_piper_cfg = _cfg.get("piper", {})
PIPER_VOICES_DIR: str | None = os.getenv("PIPER_VOICES_DIR") or _piper_cfg.get("voices_dir")
PIPER_VOICES: dict[str, dict] = _piper_cfg.get("voices", {
    "host": {"model": "ru_RU-denis-medium", "speaker": "0"},
    "guest1": {"model": "ru_RU-irina-medium", "speaker": "0"},
    "guest2": {"model": "ru_RU-dmitri-medium", "speaker": "0"},
})
_piper_available: list[dict] = _piper_cfg.get("available_voices", [
    {"id": "ru_RU-denis-medium", "name": "Денис (рус.)"},
    {"id": "ru_RU-irina-medium", "name": "Ирина (рус.)"},
    {"id": "ru_RU-dmitri-medium", "name": "Дмитрий (рус.)"},
    {"id": "ru_RU-ruslan-medium", "name": "Руслан (рус.)"},
    {"id": "en_US-lessac-medium", "name": "Lessac (англ.)"},
])
_silero_cfg = _cfg.get("silero", {})
# Скорость речи для Silero TTS (1.0 = нормально; 0.5–2.0, применяется через ffmpeg atempo)
SILERO_SPEECH_RATE: float = float(_silero_cfg.get("speech_rate", 1.0))
_silero_available: list[dict] = _silero_cfg.get("voices", [
    {"id": "silero:aidar", "name": "Silero Aidar (рус.)"},
    {"id": "silero:baya", "name": "Silero Baya (рус.)"},
    {"id": "silero:kseniya", "name": "Silero Kseniya (рус.)"},
    {"id": "silero:xenia", "name": "Silero Xenia (рус.)"},
    {"id": "silero:eugene", "name": "Silero Eugene (рус.)"},
])
AVAILABLE_VOICES: list[dict] = _piper_available + _silero_available
_tts_cfg = _cfg.get("tts", {})
PRONUNCIATION_OVERRIDES: dict[str, str] = {
    str(k): str(v)
    for k, v in (_tts_cfg.get("pronunciation_overrides", {}) or {}).items()
    if str(k).strip() and str(v).strip()
}
_music_cfg = _cfg.get("music", {})
MUSIC_ASSETS_DIR: str = os.getenv("MUSIC_ASSETS_DIR", _music_cfg.get("assets_dir", str(BASE_DIR / "assets" / "music")))
MUSIC_SETTINGS: dict[str, object] = {
    "enabled": bool(_music_cfg.get("enabled", False)),
    "intro_file": str(_music_cfg.get("intro_file", "intro.mp3")),
    "background_file": str(_music_cfg.get("background_file", "background.mp3")),
    "outro_file": str(_music_cfg.get("outro_file", "outro.mp3")),
    "intro_volume": float(_music_cfg.get("intro_volume", 0.85)),
    "background_volume": float(_music_cfg.get("background_volume", 0.10)),
    "outro_volume": float(_music_cfg.get("outro_volume", 0.90)),
}
_post_cfg = _cfg.get("postprocess", {})
POSTPROCESS_SETTINGS: dict[str, object] = {
    "enabled": bool(_post_cfg.get("enabled", False)),
    "loudnorm": bool(_post_cfg.get("loudnorm", True)),
    "compressor": bool(_post_cfg.get("compressor", True)),
    "limiter": bool(_post_cfg.get("limiter", True)),
    "target_lufs": float(_post_cfg.get("target_lufs", -16.0)),
    "true_peak_db": float(_post_cfg.get("true_peak_db", -1.5)),
    "lra": float(_post_cfg.get("lra", 11.0)),
}
_llm_roles_cfg = _cfg.get("llm_role_overrides", {})
ROLE_LLM_OVERRIDES: dict[str, dict[str, str]] = {
    str(role).strip(): {
        "model": str((cfg or {}).get("model", "")).strip(),
        **({"base_url": str((cfg or {}).get("base_url", "")).strip()} if str((cfg or {}).get("base_url", "")).strip() else {}),
    }
    for role, cfg in (_llm_roles_cfg or {}).items()
    if str(role).strip() and isinstance(cfg, dict) and str(cfg.get("model", "")).strip()
}
_style_profiles_cfg = _cfg.get("style_profiles", [])
STYLE_PROFILES: list[dict[str, str]] = (
    _style_profiles_cfg
    if isinstance(_style_profiles_cfg, list) and _style_profiles_cfg
    else [
        {
            "id": "conversational",
            "name": "Разговорный",
            "instruction": "Тон дружелюбный и естественный, короткие реплики, простой язык.",
        },
        {
            "id": "educational",
            "name": "Образовательный",
            "instruction": "Структурируй материал: тезис, объяснение, пример, вывод. Избегай пустой болтовни.",
        },
        {
            "id": "debate",
            "name": "Дебаты",
            "instruction": "Контраст позиций между спикерами, аргументы и контраргументы, но без конфликтной токсичности.",
        },
        {
            "id": "interview",
            "name": "Интервью",
            "instruction": "Ведущий задаёт вопросы, гости отвечают развёрнуто и предметно, с уточняющими вопросами.",
        },
        {
            "id": "news",
            "name": "Новости",
            "instruction": "Сухой информационный стиль, факты и контекст, минимум эмоциональных оценок.",
        },
        {
            "id": "deepdive",
            "name": "Deep Dive",
            "instruction": "Глубокий аналитический разбор, причинно-следственные связи и практические выводы.",
        },
    ]
)
STYLE_PROFILES_FILE = DATA_DIR / "style_profiles.json"


def get_voice_settings() -> dict:
    """Текущие голоса и список доступных для выбора (Piper + Silero)."""
    return {"voices": PIPER_VOICES, "available": AVAILABLE_VOICES}


def update_voice_settings(voices: dict[str, dict]) -> dict:
    """Обновить голоса (persist в config.yaml). Обновляет глобальный PIPER_VOICES на месте."""
    global PIPER_VOICES
    PIPER_VOICES.clear()
    PIPER_VOICES.update(voices)
    cfg = _load_yaml()
    cfg.setdefault("piper", {})
    cfg["piper"]["voices"] = dict(PIPER_VOICES)
    _save_yaml(cfg)
    return get_voice_settings()


def get_pronunciation_overrides() -> dict[str, str]:
    """Return current user pronunciation dictionary used by TTS normalizer."""
    return dict(PRONUNCIATION_OVERRIDES)


def update_pronunciation_overrides(overrides: dict[str, str]) -> dict[str, str]:
    """Update pronunciation dictionary and persist into config.yaml."""
    global PRONUNCIATION_OVERRIDES
    cleaned = {
        str(k).strip(): str(v).strip()
        for k, v in (overrides or {}).items()
        if str(k).strip() and str(v).strip()
    }
    PRONUNCIATION_OVERRIDES.clear()
    PRONUNCIATION_OVERRIDES.update(cleaned)

    cfg = _load_yaml()
    cfg.setdefault("tts", {})
    cfg["tts"]["pronunciation_overrides"] = dict(PRONUNCIATION_OVERRIDES)
    _save_yaml(cfg)
    return get_pronunciation_overrides()


def get_music_settings() -> dict:
    """Return current podcast music settings."""
    return {
        "enabled": bool(MUSIC_SETTINGS.get("enabled", False)),
        "assets_dir": str(MUSIC_ASSETS_DIR),
        "intro_file": str(MUSIC_SETTINGS.get("intro_file", "intro.mp3")),
        "background_file": str(MUSIC_SETTINGS.get("background_file", "background.mp3")),
        "outro_file": str(MUSIC_SETTINGS.get("outro_file", "outro.mp3")),
        "intro_volume": float(MUSIC_SETTINGS.get("intro_volume", 0.85)),
        "background_volume": float(MUSIC_SETTINGS.get("background_volume", 0.10)),
        "outro_volume": float(MUSIC_SETTINGS.get("outro_volume", 0.90)),
    }


def update_music_settings(payload: dict) -> dict:
    """Update podcast music settings and persist into config.yaml."""
    global MUSIC_SETTINGS, MUSIC_ASSETS_DIR

    cur = get_music_settings()
    next_cfg = {
        "enabled": bool(payload.get("enabled", cur["enabled"])),
        "assets_dir": str(payload.get("assets_dir", cur["assets_dir"])),
        "intro_file": str(payload.get("intro_file", cur["intro_file"])),
        "background_file": str(payload.get("background_file", cur["background_file"])),
        "outro_file": str(payload.get("outro_file", cur["outro_file"])),
        "intro_volume": float(payload.get("intro_volume", cur["intro_volume"])),
        "background_volume": float(payload.get("background_volume", cur["background_volume"])),
        "outro_volume": float(payload.get("outro_volume", cur["outro_volume"])),
    }
    next_cfg["intro_volume"] = max(0.0, min(2.0, next_cfg["intro_volume"]))
    next_cfg["background_volume"] = max(0.0, min(2.0, next_cfg["background_volume"]))
    next_cfg["outro_volume"] = max(0.0, min(2.0, next_cfg["outro_volume"]))

    MUSIC_SETTINGS.clear()
    MUSIC_SETTINGS.update({k: v for k, v in next_cfg.items() if k != "assets_dir"})
    MUSIC_ASSETS_DIR = str(next_cfg["assets_dir"])

    cfg = _load_yaml()
    cfg.setdefault("music", {})
    cfg["music"] = dict(next_cfg)
    _save_yaml(cfg)
    return get_music_settings()


def get_postprocess_settings() -> dict:
    return {
        "enabled": bool(POSTPROCESS_SETTINGS.get("enabled", False)),
        "loudnorm": bool(POSTPROCESS_SETTINGS.get("loudnorm", True)),
        "compressor": bool(POSTPROCESS_SETTINGS.get("compressor", True)),
        "limiter": bool(POSTPROCESS_SETTINGS.get("limiter", True)),
        "target_lufs": float(POSTPROCESS_SETTINGS.get("target_lufs", -16.0)),
        "true_peak_db": float(POSTPROCESS_SETTINGS.get("true_peak_db", -1.5)),
        "lra": float(POSTPROCESS_SETTINGS.get("lra", 11.0)),
    }


def update_postprocess_settings(payload: dict) -> dict:
    global POSTPROCESS_SETTINGS
    cur = get_postprocess_settings()
    next_cfg = {
        "enabled": bool(payload.get("enabled", cur["enabled"])),
        "loudnorm": bool(payload.get("loudnorm", cur["loudnorm"])),
        "compressor": bool(payload.get("compressor", cur["compressor"])),
        "limiter": bool(payload.get("limiter", cur["limiter"])),
        "target_lufs": float(payload.get("target_lufs", cur["target_lufs"])),
        "true_peak_db": float(payload.get("true_peak_db", cur["true_peak_db"])),
        "lra": float(payload.get("lra", cur["lra"])),
    }
    next_cfg["target_lufs"] = max(-30.0, min(-5.0, next_cfg["target_lufs"]))
    next_cfg["true_peak_db"] = max(-9.0, min(0.0, next_cfg["true_peak_db"]))
    next_cfg["lra"] = max(1.0, min(20.0, next_cfg["lra"]))

    POSTPROCESS_SETTINGS.clear()
    POSTPROCESS_SETTINGS.update(next_cfg)

    cfg = _load_yaml()
    cfg.setdefault("postprocess", {})
    cfg["postprocess"] = dict(next_cfg)
    _save_yaml(cfg)
    return get_postprocess_settings()


def get_ocr_settings() -> dict:
    return {
        "enabled": bool(OCR_SETTINGS.get("enabled", True)),
        "mode": "accurate" if str(OCR_SETTINGS.get("mode", "fast")).lower() == "accurate" else "fast",
        "lang": str(OCR_SETTINGS.get("lang", "rus+eng") or "rus+eng"),
        "min_chars": int(OCR_SETTINGS.get("min_chars", 8)),
        "max_pdf_pages": int(OCR_SETTINGS.get("max_pdf_pages", 40)),
        "max_docx_images": int(OCR_SETTINGS.get("max_docx_images", 40)),
    }


def update_ocr_settings(payload: dict) -> dict:
    global OCR_SETTINGS
    cur = get_ocr_settings()
    def _to_int(value, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(default)
    next_cfg = {
        "enabled": bool(payload.get("enabled", cur["enabled"])),
        "mode": str(payload.get("mode", cur["mode"])).strip().lower() or "fast",
        "lang": str(payload.get("lang", cur["lang"])).strip() or "rus+eng",
        "min_chars": _to_int(payload.get("min_chars", cur["min_chars"]), int(cur["min_chars"])),
        "max_pdf_pages": _to_int(payload.get("max_pdf_pages", cur["max_pdf_pages"]), int(cur["max_pdf_pages"])),
        "max_docx_images": _to_int(payload.get("max_docx_images", cur["max_docx_images"]), int(cur["max_docx_images"])),
    }
    if next_cfg["mode"] not in {"fast", "accurate"}:
        next_cfg["mode"] = "fast"
    next_cfg["min_chars"] = max(1, min(120, next_cfg["min_chars"]))
    next_cfg["max_pdf_pages"] = max(1, min(500, next_cfg["max_pdf_pages"]))
    next_cfg["max_docx_images"] = max(1, min(500, next_cfg["max_docx_images"]))

    OCR_SETTINGS.clear()
    OCR_SETTINGS.update(next_cfg)

    cfg = _load_yaml()
    cfg.setdefault("ocr", {})
    cfg["ocr"] = dict(next_cfg)
    _save_yaml(cfg)
    return get_ocr_settings()


def get_vision_ingest_settings() -> dict:
    """Return current vision ingest (VLM image description) settings."""
    return {
        "enabled": bool(VISION_INGEST_SETTINGS.get("enabled", False)),
        "base_url": str(VISION_INGEST_SETTINGS.get("base_url", "http://localhost:1234")).strip(),
        "model": str(VISION_INGEST_SETTINGS.get("model", "")).strip() or LMSTUDIO_MODEL,
        "timeout_seconds": max(5, min(300, int(VISION_INGEST_SETTINGS.get("timeout_seconds", 60)))),
        "max_images_per_document": max(1, min(100, int(VISION_INGEST_SETTINGS.get("max_images_per_document", 20)))),
    }


def update_vision_ingest_settings(payload: dict) -> dict:
    """Update vision ingest settings in memory and persist to config.yaml."""
    global VISION_INGEST_SETTINGS
    cur = get_vision_ingest_settings()

    def _to_int(value, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(default)

    next_cfg = {
        "enabled": bool(payload.get("enabled", cur["enabled"])),
        "base_url": str(payload.get("base_url", cur["base_url"])).strip() or cur["base_url"],
        "model": str(payload.get("model", cur["model"])).strip() or cur["model"],
        "timeout_seconds": max(5, min(300, _to_int(payload.get("timeout_seconds", cur["timeout_seconds"]), int(cur["timeout_seconds"])))),
        "max_images_per_document": max(1, min(100, _to_int(payload.get("max_images_per_document", cur["max_images_per_document"]), int(cur["max_images_per_document"])))),
    }

    VISION_INGEST_SETTINGS.clear()
    VISION_INGEST_SETTINGS.update(next_cfg)

    cfg = _load_yaml()
    cfg.setdefault("vision_ingest", {})
    cfg["vision_ingest"] = dict(next_cfg)
    _save_yaml(cfg)
    return get_vision_ingest_settings()


def get_role_llm_overrides() -> dict[str, dict[str, str]]:
    """Return persisted per-role LLM overrides."""
    return {
        role: {
            "model": str(cfg.get("model", "")).strip(),
            **({"base_url": str(cfg.get("base_url", "")).strip()} if str(cfg.get("base_url", "")).strip() else {}),
        }
        for role, cfg in (ROLE_LLM_OVERRIDES or {}).items()
        if str(role).strip() and isinstance(cfg, dict) and str(cfg.get("model", "")).strip()
    }


def update_role_llm_overrides(payload: dict[str, dict]) -> dict[str, dict[str, str]]:
    """Persist per-role LLM overrides into config.yaml."""
    global ROLE_LLM_OVERRIDES
    cleaned: dict[str, dict[str, str]] = {}
    for role, cfg in (payload or {}).items():
        role_name = str(role).strip()
        if not role_name:
            continue
        if not isinstance(cfg, dict):
            continue
        model = str(cfg.get("model", "")).strip()
        base_url = str(cfg.get("base_url", "")).strip()
        if not model:
            continue
        cleaned[role_name] = {"model": model, **({"base_url": base_url} if base_url else {})}

    ROLE_LLM_OVERRIDES.clear()
    ROLE_LLM_OVERRIDES.update(cleaned)

    cfg = _load_yaml()
    cfg["llm_role_overrides"] = dict(ROLE_LLM_OVERRIDES)
    _save_yaml(cfg)
    return get_role_llm_overrides()


def get_style_profiles() -> list[dict[str, str]]:
    profiles = STYLE_PROFILES
    if STYLE_PROFILES_FILE.exists():
        try:
            payload = json.loads(STYLE_PROFILES_FILE.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                profiles = payload
        except Exception:
            profiles = STYLE_PROFILES
    return [
        {
            "id": str(p.get("id", "")),
            "name": str(p.get("name", p.get("id", ""))),
            "instruction": str(p.get("instruction", "")),
        }
        for p in profiles
        if str(p.get("id", "")).strip()
    ]


def upsert_style_profile(profile: dict) -> list[dict[str, str]]:
    """Create or update a style profile in data/style_profiles.json."""
    profiles = get_style_profiles()
    raw_id = str(profile.get("id", "")).strip()
    name = str(profile.get("name", "")).strip()
    instruction = str(profile.get("instruction", "")).strip()
    if not name:
        raise ValueError("name обязателен")
    if not instruction:
        raise ValueError("instruction обязателен")
    if not raw_id:
        base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        raw_id = f"{base or 'style'}-{uuid.uuid4().hex[:4]}"

    next_profiles = []
    replaced = False
    for p in profiles:
        if p["id"] == raw_id:
            next_profiles.append({"id": raw_id, "name": name, "instruction": instruction})
            replaced = True
        else:
            next_profiles.append(p)
    if not replaced:
        next_profiles.append({"id": raw_id, "name": name, "instruction": instruction})
    STYLE_PROFILES_FILE.parent.mkdir(parents=True, exist_ok=True)
    STYLE_PROFILES_FILE.write_text(
        json.dumps(next_profiles, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return next_profiles


def delete_style_profile(profile_id: str) -> list[dict[str, str]]:
    """Delete a style profile by id from data/style_profiles.json."""
    pid = str(profile_id or "").strip()
    if not pid:
        raise ValueError("profile_id обязателен")
    profiles = get_style_profiles()
    next_profiles = [p for p in profiles if p["id"] != pid]
    STYLE_PROFILES_FILE.parent.mkdir(parents=True, exist_ok=True)
    STYLE_PROFILES_FILE.write_text(
        json.dumps(next_profiles, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return next_profiles

"""Pydantic models shared across the application."""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------- Upload / Ingest ------------------------------------------------

class UploadResponse(BaseModel):
    document_id: str
    filename: str
    duplicate: bool = False
    duplicate_of: str | None = None
    existing_ingested: bool | None = None
    message: str | None = None


class IngestResponse(BaseModel):
    document_id: str
    chunks: int
    status: str = "indexed"


# ---------- Summary --------------------------------------------------------

class SourceFragment(BaseModel):
    chunk_id: str
    text: str


class SummaryResponse(BaseModel):
    document_id: str
    summary: str
    sources: list[SourceFragment] = Field(default_factory=list)


# ---------- Voice Q&A ------------------------------------------------------

class VoiceQaSource(BaseModel):
    evidence_id: str | None = None
    anchor_id: str | None = None
    document_id: str
    chunk_id: str
    chunk_index: int | None = None
    source_locator: dict[str, Any] | None = None
    section_path: str | None = None
    page: int | None = None
    source_type: str | None = None
    anchor: str | None = None
    caption: str | None = None
    score: float | None = None
    text: str = ""
    highlights: list[str] = Field(default_factory=list)
    matched_terms: list[str] = Field(default_factory=list)


class VoiceQaResponse(BaseModel):
    document_id: str
    question_text: str
    answer_text: str
    sources: list[VoiceQaSource] = Field(default_factory=list)
    confidence: float | None = None
    confidence_breakdown: dict[str, float] | None = None
    mode: str | None = None
    knowledge_mode: str | None = None
    effective_knowledge_mode: str | None = None
    has_model_knowledge_content: bool = False
    audio_filename: str | None = None
    audio_duration_sec: float | None = None
    stt_model: str | None = None


# ---------- Podcast script -------------------------------------------------

class RoleLlmConfig(BaseModel):
    model: str
    base_url: str | None = None


class PodcastScriptRequest(BaseModel):
    minutes: int = Field(default=5, ge=1, le=60, description="Target podcast duration in minutes.")
    style: str = Field(default="conversational", description="Style profile id for script generation.")
    focus: str | None = Field(
        default=None,
        description="Optional user focus/topic for script generation and chunk retrieval.",
    )
    voices: list[str] = Field(
        default_factory=lambda: ["host", "guest1", "guest2"],
        description="Ordered list of speaker roles used in generated dialogue.",
    )
    scenario: str = Field(
        default="classic_overview",
        description="Script scenario id (classic_overview, interview, debate, etc.).",
    )
    scenario_options: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional scenario-specific generation parameters.",
    )
    generation_mode: str = Field(
        default="single_pass",
        description="Generation mode: single_pass or turn_taking.",
    )
    role_llm_map: dict[str, RoleLlmConfig] | None = Field(
        default=None,
        description="Optional per-role LLM config. In single-pass mode, backend may use one primary role config as override.",
    )
    outline_plan: dict[str, Any] | None = Field(
        default=None,
        description="Optional approved/edited outline plan for turn-taking generation.",
    )
    tts_friendly: bool = Field(
        default=True,
        description="Enable TTS-friendly text rewrite (stress marks, transliteration, numbers as words).",
    )
    knowledge_mode: str = Field(
        default="document_only",
        description="Knowledge mode: document_only or hybrid_model.",
    )


class DialogueLine(BaseModel):
    voice: str
    text: str
    grounding: str | None = None


class PodcastScriptResponse(BaseModel):
    document_id: str
    script: list[DialogueLine]
    knowledge_mode: str | None = None
    effective_knowledge_mode: str | None = None


# ---------- Jobs -----------------------------------------------------------

class JobStatus(str, Enum):
    pending = "pending"
    running = "running"
    retrying = "retrying"
    done = "done"
    error = "error"
    cancelled = "cancelled"


class JobInfo(BaseModel):
    job_id: str
    status: JobStatus
    progress: int = Field(default=0, ge=0, le=100)
    lane: str = "default"
    lane_limit: int | None = None
    lane_running: int | None = None
    lane_pending: int | None = None
    queue_position: int | None = None
    output_paths: list[str] = Field(default_factory=list)
    error: Optional[str] = None
    cancel_requested: bool = False
    job_type: str | None = None
    recipe: dict[str, Any] | None = None
    parent_job_id: str | None = None


class AudioJobRequest(BaseModel):
    pass  # uses script from previous generation


# ---------- Settings -------------------------------------------------------

class LMStudioSettings(BaseModel):
    base_url: str = "http://localhost:1234/v1"
    model: str = "local-model"
    temperature: float = 0.4
    max_tokens: int = 4096


class VoiceOption(BaseModel):
    id: str
    name: str


class VoiceSlot(BaseModel):
    model: str
    speaker: str = "0"


class VoiceSettingsResponse(BaseModel):
    voices: dict[str, VoiceSlot]
    available: list[VoiceOption]


class RoleLlmSettingsResponse(BaseModel):
    role_llm_map: dict[str, RoleLlmConfig] = Field(default_factory=dict)


class MusicSettings(BaseModel):
    enabled: bool = False
    assets_dir: str = "/opt/audio-assets"
    intro_file: str = "intro.mp3"
    background_file: str = "background.mp3"
    outro_file: str = "outro.mp3"
    intro_volume: float = Field(default=0.85, ge=0.0, le=2.0)
    background_volume: float = Field(default=0.10, ge=0.0, le=2.0)
    outro_volume: float = Field(default=0.90, ge=0.0, le=2.0)


class PostprocessSettings(BaseModel):
    enabled: bool = False
    loudnorm: bool = True
    compressor: bool = True
    limiter: bool = True
    target_lufs: float = Field(default=-16.0, ge=-30.0, le=-5.0)
    true_peak_db: float = Field(default=-1.5, ge=-9.0, le=0.0)
    lra: float = Field(default=11.0, ge=1.0, le=20.0)


class OcrSettings(BaseModel):
    enabled: bool = True
    mode: str = Field(default="fast", pattern="^(fast|accurate)$")
    lang: str = "rus+eng"
    min_chars: int = Field(default=8, ge=1, le=120)
    max_pdf_pages: int = Field(default=40, ge=1, le=500)
    max_docx_images: int = Field(default=40, ge=1, le=500)

"""TTS dispatcher: route each script line to Piper or Silero by voice model id."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path

from app.audio.music_postprocess import apply_music_to_mp3
from app.audio.postprocess import apply_postprocess_to_mp3
from app.config import OUTPUTS_DIR, get_voice_settings
from app.models import DialogueLine
from app.tts.piper_engine import PiperEngine
from app.tts.silero_engine import SILERO_PREFIX, SileroEngine
from app.tts.utils import voice_cfg_for_slot

# Единая частота и каналы для склейки (Piper ≈ 22050 Hz, Silero 48 kHz → приводим к одному)
TTS_CONCAT_SAMPLE_RATE = 48000
TTS_CONCAT_CHANNELS = 1

_piper: PiperEngine | None = None
_silero: SileroEngine | None = None


def _get_piper() -> PiperEngine:
    global _piper
    if _piper is None:
        _piper = PiperEngine()
    return _piper


def _get_silero() -> SileroEngine:
    global _silero
    if _silero is None:
        _silero = SileroEngine()
    return _silero


def _engine_for_model(model_id: str) -> str:
    """Return 'silero' or 'piper' for the given model id."""
    if model_id and str(model_id).strip().lower().startswith(SILERO_PREFIX):
        return "silero"
    return "piper"


async def _normalize_wav(ffmpeg: str, src: Path, dst: Path) -> None:
    """Привести WAV к единым sample rate и моно для корректной склейки Piper + Silero."""
    proc = await asyncio.create_subprocess_exec(
        ffmpeg, "-y", "-i", str(src),
        "-ar", str(TTS_CONCAT_SAMPLE_RATE),
        "-ac", str(TTS_CONCAT_CHANNELS),
        "-acodec", "pcm_s16le",
        str(dst),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"ffmpeg normalize failed for {src}: {detail or 'unknown error'}")


async def synthesise_script(
    script: list[DialogueLine],
    document_id: str,
    progress_cb=None,
    apply_music: bool = True,
    apply_postprocess: bool = True,
) -> Path:
    """Render script to one MP3: each line via Piper or Silero, then concat + encode."""
    voices = get_voice_settings().get("voices", {})
    piper_engine = _get_piper()
    silero_engine = _get_silero()
    piper_engine._check_deps()
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg не найден")

    workdir = Path(tempfile.mkdtemp(prefix="tts_"))
    wav_files: list[Path] = []
    total = len(script)

    for i, line in enumerate(script):
        # script может быть как списком DialogueLine, так и списком dict
        if isinstance(line, dict):
            line_obj = DialogueLine(**line)
        else:
            line_obj = line
        wav_path = workdir / f"line_{i:04d}.wav"
        slot_cfg = voice_cfg_for_slot(voices, line_obj.voice)
        model_id = (slot_cfg.get("model") or "").strip()
        engine_kind = _engine_for_model(model_id)

        if engine_kind == "silero":
            await silero_engine.synthesise_line(line_obj, wav_path)
        else:
            await piper_engine.synthesise_line(line_obj, wav_path)

        wav_files.append(wav_path)
        if progress_cb:
            await progress_cb(int((i + 1) / total * 80))

    # Нормализация: Piper 22050 Hz, Silero 48 kHz → один формат, иначе склейка даёт разную скорость
    normalized: list[Path] = []
    for i, wav in enumerate(wav_files):
        norm_path = workdir / f"line_{i:04d}_norm.wav"
        await _normalize_wav(ffmpeg, wav, norm_path)
        normalized.append(norm_path)
    if progress_cb:
        await progress_cb(85)

    list_file = workdir / "concat.txt"
    list_file.write_text(
        "\n".join(f"file '{w}'" for w in normalized),
        encoding="utf-8",
    )
    concat_wav = workdir / "full.wav"
    proc = await asyncio.create_subprocess_exec(
        ffmpeg, "-y", "-f", "concat", "-safe", "0",
        "-i", str(list_file), "-c", "copy", str(concat_wav),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"ffmpeg concat failed: {detail or 'unknown error'}")

    if progress_cb:
        await progress_cb(90)

    out_mp3 = OUTPUTS_DIR / f"{document_id}_podcast.mp3"
    proc = await asyncio.create_subprocess_exec(
        ffmpeg, "-y", "-i", str(concat_wav),
        "-codec:a", "libmp3lame", "-b:a", "192k",
        str(out_mp3),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"ffmpeg mp3 encode failed: {detail or 'unknown error'}")

    if apply_music:
        await asyncio.to_thread(apply_music_to_mp3, out_mp3)
    if apply_postprocess:
        await asyncio.to_thread(apply_postprocess_to_mp3, out_mp3)

    if progress_cb:
        await progress_cb(100)

    shutil.rmtree(workdir, ignore_errors=True)
    return out_mp3

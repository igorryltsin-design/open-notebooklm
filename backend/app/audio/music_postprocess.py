"""Offline background music/jingle post-processing for podcast audio."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from app.config import get_music_settings


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "ffmpeg command failed")


def _to_wav(ffmpeg: str, src: Path, dst: Path, volume: float = 1.0) -> None:
    _run(
        [
            ffmpeg, "-y", "-i", str(src),
            "-af", f"volume={volume}",
            "-ar", "48000", "-ac", "1", "-acodec", "pcm_s16le",
            str(dst),
        ]
    )


def _mix_bg(ffmpeg: str, voice_wav: Path, bg_path: Path, bg_volume: float, out_wav: Path) -> None:
    _run(
        [
            ffmpeg, "-y",
            "-stream_loop", "-1", "-i", str(bg_path),
            "-i", str(voice_wav),
            "-filter_complex",
            f"[0:a]volume={bg_volume},aformat=sample_rates=48000:channel_layouts=mono[bg];"
            "[1:a]aformat=sample_rates=48000:channel_layouts=mono[v];"
            "[bg][v]amix=inputs=2:duration=shortest:dropout_transition=0[m]",
            "-map", "[m]",
            "-ar", "48000", "-ac", "1", "-acodec", "pcm_s16le",
            str(out_wav),
        ]
    )


def _concat_wavs(ffmpeg: str, parts: list[Path], out_wav: Path) -> None:
    if len(parts) == 1:
        shutil.copy(parts[0], out_wav)
        return
    with tempfile.TemporaryDirectory(prefix="concat_") as td:
        list_file = Path(td) / "list.txt"
        list_file.write_text("\n".join(f"file '{p}'" for p in parts), encoding="utf-8")
        _run([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(out_wav)])


def apply_music_to_mp3(mp3_path: Path) -> Path:
    """Apply intro/background/outro from local assets dir to an existing MP3."""
    cfg = get_music_settings()
    if not cfg.get("enabled"):
        return mp3_path

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return mp3_path

    assets_dir = Path(str(cfg.get("assets_dir", "")))
    if not assets_dir.exists():
        return mp3_path

    intro = assets_dir / str(cfg.get("intro_file", "intro.mp3"))
    bg = assets_dir / str(cfg.get("background_file", "background.mp3"))
    outro = assets_dir / str(cfg.get("outro_file", "outro.mp3"))

    with tempfile.TemporaryDirectory(prefix="music_") as td:
        td_path = Path(td)
        voice_wav = td_path / "voice.wav"
        mix_wav = td_path / "mix.wav"
        final_wav = td_path / "final.wav"

        _to_wav(ffmpeg, mp3_path, voice_wav, volume=1.0)
        if bg.exists():
            _mix_bg(
                ffmpeg,
                voice_wav=voice_wav,
                bg_path=bg,
                bg_volume=float(cfg.get("background_volume", 0.10)),
                out_wav=mix_wav,
            )
        else:
            shutil.copy(voice_wav, mix_wav)

        parts: list[Path] = []
        if intro.exists():
            intro_wav = td_path / "intro.wav"
            _to_wav(ffmpeg, intro, intro_wav, volume=float(cfg.get("intro_volume", 0.85)))
            parts.append(intro_wav)
        parts.append(mix_wav)
        if outro.exists():
            outro_wav = td_path / "outro.wav"
            _to_wav(ffmpeg, outro, outro_wav, volume=float(cfg.get("outro_volume", 0.90)))
            parts.append(outro_wav)

        _concat_wavs(ffmpeg, parts, final_wav)
        out_mp3 = td_path / "final.mp3"
        _run([ffmpeg, "-y", "-i", str(final_wav), "-codec:a", "libmp3lame", "-b:a", "192k", str(out_mp3)])
        shutil.copy(out_mp3, mp3_path)

    return mp3_path


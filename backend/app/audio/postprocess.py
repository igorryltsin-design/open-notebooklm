"""Offline mastering/post-processing for podcast audio."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from app.config import get_postprocess_settings


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "ffmpeg postprocess failed")


def apply_postprocess_to_mp3(mp3_path: Path) -> Path:
    """Apply loudness normalization/compression/limiter to final MP3."""
    cfg = get_postprocess_settings()
    if not cfg.get("enabled"):
        return mp3_path

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return mp3_path

    filters: list[str] = []
    if cfg.get("loudnorm", True):
        filters.append(
            f"loudnorm=I={float(cfg.get('target_lufs', -16.0))}:"
            f"TP={float(cfg.get('true_peak_db', -1.5))}:LRA={float(cfg.get('lra', 11.0))}"
        )
    if cfg.get("compressor", True):
        filters.append("acompressor=threshold=-18dB:ratio=3:attack=20:release=250")
    if cfg.get("limiter", True):
        filters.append("alimiter=limit=0.95")

    if not filters:
        return mp3_path

    with tempfile.TemporaryDirectory(prefix="mastering_") as td:
        out_mp3 = Path(td) / "post.mp3"
        _run(
            [
                ffmpeg, "-y", "-i", str(mp3_path),
                "-af", ",".join(filters),
                "-codec:a", "libmp3lame", "-b:a", "192k",
                str(out_mp3),
            ]
        )
        shutil.copy(out_mp3, mp3_path)
    return mp3_path


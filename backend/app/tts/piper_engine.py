"""Text-to-speech via Piper CLI → WAV → ffmpeg → MP3."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path

from app.config import BASE_DIR, OUTPUTS_DIR, PIPER_BINARY, PIPER_VOICES, PIPER_VOICES_DIR
from app.models import DialogueLine
from app.tts.utils import voice_cfg_for_slot

logger = logging.getLogger(__name__)

# macOS: Piper может искать libespeak-ng в Homebrew
def _piper_env() -> dict:
    env = os.environ.copy()
    if os.name != "nt":
        for libdir in ("/opt/homebrew/lib", "/usr/local/lib"):
            if Path(libdir).exists():
                key = "DYLD_LIBRARY_PATH" if os.uname().sysname == "Darwin" else "LD_LIBRARY_PATH"
                env[key] = libdir + os.pathsep + env.get(key, "")
                break
    return env


class PiperEngine:
    """Synthesise speech with Piper (offline, local)."""

    def __init__(self) -> None:
        self.piper_bin = PIPER_BINARY
        self.voices = PIPER_VOICES
        self._ffmpeg = shutil.which("ffmpeg")
        # Support absolute path to local piper (e.g. .../backend/piper_bin/piper)
        piper_path = Path(self.piper_bin)
        if piper_path.is_absolute() and piper_path.is_file():
            self._piper_path = str(piper_path)
        else:
            self._piper_path = shutil.which(self.piper_bin) or self.piper_bin

    def _check_deps(self) -> None:
        """Raise with a clear message if piper or ffmpeg is missing."""
        missing = []
        if not self._ffmpeg:
            missing.append("ffmpeg")
        piper_ok = Path(self._piper_path).is_file() if self._piper_path else False
        if not piper_ok and not shutil.which(self.piper_bin):
            missing.append("piper (TTS)")
        if missing:
            raise RuntimeError(
                "Для генерации аудио нужны: "
                + ", ".join(missing)
                + ". Установите их и добавьте в PATH (ffmpeg: brew install ffmpeg; Piper: https://github.com/rhasspy/piper/releases)."
            )

    # ------------------------------------------------------------------
    async def synthesise_line(
        self,
        line: DialogueLine,
        out_wav: Path,
    ) -> Path:
        """Render a single dialogue line to WAV."""
        voice_cfg = voice_cfg_for_slot(self.voices, line.voice)

        model = voice_cfg["model"]
        speaker = voice_cfg.get("speaker", "0")

        # Путь к модели: PIPER_VOICES_DIR (Docker), piper_bin/voices, иначе имя для Piper
        model_path = voice_cfg.get("path") or model
        if not Path(model_path).is_absolute():
            fname = model_path if model_path.endswith(".onnx") else f"{model_path}.onnx"
            search_dirs = []
            if PIPER_VOICES_DIR:
                search_dirs.append(Path(PIPER_VOICES_DIR))
            if self._piper_path:
                p = Path(self._piper_path).parent
                search_dirs.extend([p / "voices", p])
            search_dirs.extend([BASE_DIR / "piper_bin" / "voices", BASE_DIR / "piper_bin"])
            for d in search_dirs:
                if d.exists() and (d / fname).exists():
                    model_path = str(d / fname)
                    break

        cmd = [
            self._piper_path,
            "--model", model_path,
            "--speaker", str(speaker),
            "--output_file", str(out_wav),
        ]
        cwd = Path(self._piper_path).parent if Path(self._piper_path).is_file() else None

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=_piper_env(),
        )
        stdout, stderr = await proc.communicate(input=line.text.encode("utf-8"))

        if proc.returncode != 0:
            err = stderr.decode(errors="replace")
            raise RuntimeError(f"Piper failed (exit {proc.returncode}): {err}")

        return out_wav

    # ------------------------------------------------------------------
    async def synthesise_script(
        self,
        script: list[DialogueLine],
        document_id: str,
        progress_cb=None,
    ) -> Path:
        """Render full script to a single MP3 file.

        1. Synthesise each line → WAV
        2. Concatenate WAVs with ffmpeg
        3. Encode to MP3
        """
        self._check_deps()
        workdir = Path(tempfile.mkdtemp(prefix="piper_"))
        wav_files: list[Path] = []

        total = len(script)
        for i, line in enumerate(script):
            wav_path = workdir / f"line_{i:04d}.wav"
            await self.synthesise_line(line, wav_path)
            wav_files.append(wav_path)
            if progress_cb:
                await progress_cb(int((i + 1) / total * 80))

        # Build ffmpeg concat list
        list_file = workdir / "concat.txt"
        list_file.write_text(
            "\n".join(f"file '{w}'" for w in wav_files),
            encoding="utf-8",
        )

        concat_wav = workdir / "full.wav"
        proc = await asyncio.create_subprocess_exec(
            self._ffmpeg, "-y", "-f", "concat", "-safe", "0",
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

        # Encode to MP3
        out_mp3 = OUTPUTS_DIR / f"{document_id}_podcast.mp3"
        proc = await asyncio.create_subprocess_exec(
            self._ffmpeg, "-y", "-i", str(concat_wav),
            "-codec:a", "libmp3lame", "-b:a", "192k",
            str(out_mp3),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"ffmpeg mp3 encode failed: {detail or 'unknown error'}")

        if progress_cb:
            await progress_cb(100)

        # Cleanup temp files
        shutil.rmtree(workdir, ignore_errors=True)

        logger.info("Podcast MP3 saved: %s", out_mp3)
        return out_mp3

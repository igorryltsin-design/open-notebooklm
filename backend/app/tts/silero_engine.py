"""Text-to-speech via Silero TTS (Russian voices) → WAV → ffmpeg → MP3."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import torch
import torchaudio

from app.config import OUTPUTS_DIR, PIPER_VOICES, SILERO_SPEECH_RATE
from app.models import DialogueLine
from app.tts.text_normalize import latin_to_russian_readable, plus_stress_to_unicode
from app.tts.ssml_pauses import split_text_by_pauses, text_to_ssml
from app.tts.utils import voice_cfg_for_slot

logger = logging.getLogger(__name__)

SILERO_PREFIX = "silero:"
SILERO_RU_SPEAKERS = ("aidar", "baya", "kseniya", "xenia", "eugene")
SILERO_SAMPLE_RATE = 48000


def _get_silero_models_dir() -> Path | None:
    raw = os.getenv("SILERO_MODELS_DIR") or os.getenv("TORCH_HOME")
    if raw:
        return Path(raw)
    return None


def _parse_silero_voice(model_id: str, allowed_speakers: tuple[str, ...] | None = None) -> str | None:
    """Return speaker name if model_id is silero:speaker and speaker is allowed, else None."""
    if not model_id.startswith(SILERO_PREFIX):
        return None
    speaker = model_id[len(SILERO_PREFIX) :].strip().lower()
    allowed = allowed_speakers if allowed_speakers is not None else SILERO_RU_SPEAKERS
    return speaker if speaker in allowed else None


class SileroEngine:
    """Synthesise speech with Silero TTS (Russian, offline)."""

    def __init__(self) -> None:
        self._ffmpeg = shutil.which("ffmpeg")
        self._voices = PIPER_VOICES  # slot -> {model, speaker}; model can be "silero:aidar"
        self._model = None
        self._speakers = SILERO_RU_SPEAKERS  # updated from model.speakers after load
        self._device = torch.device("cpu")
        _dir = _get_silero_models_dir()
        if _dir:
            os.environ["TORCH_HOME"] = str(_dir)

    def _get_model(self):
        """Lazy-load Silero Russian v5 *only* from local v5_ru.pt (offline, no hub)."""
        if self._model is None:
            models_dir = _get_silero_models_dir()
            if not models_dir:
                raise RuntimeError(
                    "Не найден каталог с моделями Silero. "
                    "Ожидается, что переменная окружения SILERO_MODELS_DIR или "
                    "TORCH_HOME указывает на папку с v5_ru.pt."
                )

            os.environ["TORCH_HOME"] = str(models_dir)
            local_pt = models_dir / "v5_ru.pt"
            if not local_pt.is_file():
                raise RuntimeError(
                    f"Файл модели Silero v5_ru.pt не найден по пути {local_pt}"
                )

            try:
                imp = torch.package.PackageImporter(str(local_pt))
                model = imp.load_pickle("tts_models", "model")
            except Exception as e:  # pragma: no cover - защитный блок
                logger.error("Silero load from .pt failed (%s): %s", local_pt, e)
                raise RuntimeError(
                    f"Не удалось загрузить модель Silero из {local_pt}: {e}"
                ) from e

            if model is None:
                raise RuntimeError("Модель Silero загружена как None из v5_ru.pt")

            # В этой модели .to(...) работает in-place и возвращает None,
            # поэтому вызываем без переназначения.
            model.to(self._device)
            self._model = model
            # Список спикеров из модели (источник истины), иначе fallback на константу
            if hasattr(model, "speakers") and isinstance(model.speakers, (list, tuple)):
                try:
                    self._speakers = tuple(str(s) for s in model.speakers)
                except (TypeError, ValueError):
                    self._speakers = SILERO_RU_SPEAKERS
            else:
                self._speakers = SILERO_RU_SPEAKERS

        return self._model

    def _check_deps(self) -> None:
        if not self._ffmpeg:
            raise RuntimeError(
                "Для генерации аудио нужен ffmpeg. Установите: brew install ffmpeg"
            )

    def _silero_tts_kwargs(self) -> dict:
        """Common kwargs for apply_tts / save_wav (V5: accent, yo, homographs)."""
        return {
            "put_accent": True,
            "put_yo": True,
            "put_stress_homo": True,
            "put_yo_homo": True,
        }

    def _render_plain_to_wav(self, text: str, speaker: str, out_wav: Path) -> None:
        """Render plain text to WAV (no SSML, no pause splitting). Used by SSML fallback."""
        model = self._get_model()
        tts_kw = self._silero_tts_kwargs()
        if hasattr(model, "save_wav") and callable(getattr(model, "save_wav")):
            try:
                model.save_wav(
                    text=text,
                    speaker=speaker,
                    sample_rate=SILERO_SAMPLE_RATE,
                    audio_path=str(out_wav),
                    **tts_kw,
                )
            except TypeError:
                model.save_wav(
                    text=text,
                    speaker=speaker,
                    sample_rate=SILERO_SAMPLE_RATE,
                    audio_path=str(out_wav),
                )
            return
        try:
            audio = model.apply_tts(
                text=text,
                speaker=speaker,
                sample_rate=SILERO_SAMPLE_RATE,
                **tts_kw,
            )
        except TypeError:
            audio = model.apply_tts(
                text=text,
                speaker=speaker,
                sample_rate=SILERO_SAMPLE_RATE,
                put_accent=True,
                put_yo=True,
            )
        import numpy as np
        if torch.is_tensor(audio):
            waveform = audio.unsqueeze(0)
        else:
            arr = np.asarray(audio, dtype=np.float32)
            waveform = torch.from_numpy(arr).unsqueeze(0)
        torchaudio.save(str(out_wav), waveform, SILERO_SAMPLE_RATE)

    def _synthesise_line_sync(self, text: str, speaker: str, out_wav: Path) -> None:
        """Blocking synthesis; run in thread. Supports SSML or [PAUSE_*] markers with fallback."""
        model = self._get_model()
        if speaker not in self._speakers:
            raise ValueError(
                f"Silero speaker {speaker!r} not in model.speakers: {self._speakers}"
            )
        text = latin_to_russian_readable(text)
        # Модель из v5_ru.pt может озвучивать '+' буквально; конвертируем в Unicode-ударение (́)
        text = plus_stress_to_unicode(text)
        ssml_str, has_ssml = text_to_ssml(text)
        tts_kw = self._silero_tts_kwargs()

        if has_ssml:
            # Try SSML path (apply_tts/save_wav with ssml_text=)
            try:
                if hasattr(model, "save_wav") and callable(getattr(model, "save_wav")):
                    try:
                        model.save_wav(
                            ssml_text=ssml_str,
                            speaker=speaker,
                            sample_rate=SILERO_SAMPLE_RATE,
                            audio_path=str(out_wav),
                            **tts_kw,
                        )
                    except TypeError:
                        raise
                    else:
                        return
                audio = model.apply_tts(
                    ssml_text=ssml_str,
                    speaker=speaker,
                    sample_rate=SILERO_SAMPLE_RATE,
                    **tts_kw,
                )
                import numpy as np
                if torch.is_tensor(audio):
                    waveform = audio.unsqueeze(0)
                else:
                    arr = np.asarray(audio, dtype=np.float32)
                    waveform = torch.from_numpy(arr).unsqueeze(0)
                torchaudio.save(str(out_wav), waveform, SILERO_SAMPLE_RATE)
                return
            except (TypeError, AttributeError) as e:
                logger.debug("Silero SSML not supported, using pause fallback: %s", e)

            # Fallback: split by pauses, render segments, insert silence, concat
            segments = split_text_by_pauses(text)
            if not self._ffmpeg:
                raise RuntimeError("ffmpeg required for pause fallback")
            workdir = Path(tempfile.mkdtemp(prefix="silero_pause_"))
            wavs: list[Path] = []
            try:
                for i, (seg, pause_sec) in enumerate(segments):
                    if seg:
                        seg_wav = workdir / f"seg_{i:04d}.wav"
                        self._render_plain_to_wav(seg, speaker, seg_wav)
                        wavs.append(seg_wav)
                    if pause_sec > 0:
                        silence_wav = workdir / f"silence_{i:04d}.wav"
                        subprocess.run(
                            [
                                self._ffmpeg, "-y", "-f", "lavfi",
                                "-i", f"anullsrc=r={SILERO_SAMPLE_RATE}:cl=mono",
                                "-t", str(pause_sec),
                                "-acodec", "pcm_s16le",
                                str(silence_wav),
                            ],
                            capture_output=True,
                            check=False,
                        )
                        if silence_wav.exists():
                            wavs.append(silence_wav)
                if not wavs:
                    self._render_plain_to_wav(" ", speaker, out_wav)
                    return
                if len(wavs) == 1:
                    shutil.copy(wavs[0], out_wav)
                    return
                list_file = workdir / "concat.txt"
                list_file.write_text(
                    "\n".join(f"file '{w.absolute()}'" for w in wavs),
                    encoding="utf-8",
                )
                subprocess.run(
                    [
                        self._ffmpeg, "-y", "-f", "concat", "-safe", "0",
                        "-i", str(list_file), "-c", "copy", str(out_wav),
                    ],
                    capture_output=True,
                    check=True,
                )
            finally:
                shutil.rmtree(workdir, ignore_errors=True)
            return

        # No SSML: plain text path
        self._render_plain_to_wav(text, speaker, out_wav)

    async def synthesise_line(
        self,
        line: DialogueLine,
        out_wav: Path,
    ) -> Path:
        """Render a single dialogue line to WAV using Silero."""
        voice_cfg = voice_cfg_for_slot(self._voices, line.voice)
        model_id = voice_cfg.get("model", "")
        speaker = _parse_silero_voice(model_id, allowed_speakers=self._speakers)
        if not speaker:
            raise ValueError(
                f"SileroEngine expects model id like silero:aidar, got {model_id!r}"
            )
        await asyncio.to_thread(
            self._synthesise_line_sync, line.text, speaker, out_wav
        )
        return out_wav

    async def synthesise_script(
        self,
        script: list[DialogueLine],
        document_id: str,
        progress_cb=None,
    ) -> Path:
        """Render full script to a single MP3 (same pipeline as Piper)."""
        self._check_deps()
        workdir = Path(tempfile.mkdtemp(prefix="silero_"))
        wav_files: list[Path] = []
        total = len(script)
        for i, line in enumerate(script):
            wav_path = workdir / f"line_{i:04d}.wav"
            await self.synthesise_line(line, wav_path)
            wav_files.append(wav_path)
            if progress_cb:
                await progress_cb(int((i + 1) / total * 80))

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

        # Опционально: скорость речи через ffmpeg atempo (0.5–2.0)
        wav_for_mp3 = concat_wav
        rate = max(0.5, min(2.0, float(SILERO_SPEECH_RATE)))
        if abs(rate - 1.0) > 0.01:
            adj_wav = workdir / "full_adj.wav"
            proc = await asyncio.create_subprocess_exec(
                self._ffmpeg, "-y", "-i", str(concat_wav),
                "-filter:a", f"atempo={rate}",
                str(adj_wav),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _stdout, _stderr = await proc.communicate()
            if adj_wav.exists():
                wav_for_mp3 = adj_wav

        out_mp3 = OUTPUTS_DIR / f"{document_id}_podcast.mp3"
        proc = await asyncio.create_subprocess_exec(
            self._ffmpeg, "-y", "-i", str(wav_for_mp3),
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

        shutil.rmtree(workdir, ignore_errors=True)
        logger.info("Podcast MP3 saved: %s", out_mp3)
        return out_mp3

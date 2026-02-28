"""SSML and pause markers for Silero TTS: [PAUSE_SHORT] -> <break time="0.5s"/> etc."""

from __future__ import annotations

import re
from typing import Tuple

# Markers and their durations in seconds
PAUSE_SHORT = 0.5
PAUSE_LONG = 2.0
MARKER_PATTERN = re.compile(
    r"\[PAUSE_SHORT\]|\[PAUSE_LONG\]|\[PAUSE_(\d+(?:\.\d+)?)s?\]",
    re.IGNORECASE,
)


def _pause_seconds(match: re.Match) -> float:
    if match.group(0).upper().startswith("[PAUSE_SHORT]"):
        return PAUSE_SHORT
    if match.group(0).upper().startswith("[PAUSE_LONG]"):
        return PAUSE_LONG
    return float(match.group(1))


def text_to_ssml(text: str) -> Tuple[str, bool]:
    """Replace pause markers with SSML <break/>. Return (ssml_string, True) if any marker was present."""
    if not MARKER_PATTERN.search(text):
        return text, False
    def repl(m: re.Match) -> str:
        s = _pause_seconds(m)
        return f'<break time="{s}s"/>'
    out = MARKER_PATTERN.sub(repl, text)
    out = f"<speak>{out}</speak>"
    return out, True


def split_text_by_pauses(text: str) -> list[Tuple[str, float]]:
    """Split text by pause markers; return [(segment_text, pause_after_seconds), ...]."""
    parts: list[Tuple[str, float]] = []
    last_end = 0
    for m in MARKER_PATTERN.finditer(text):
        segment = text[last_end : m.start()].strip()
        if segment:
            parts.append((segment, 0.0))
        parts.append(("", _pause_seconds(m)))
        last_end = m.end()
    tail = text[last_end:].strip()
    if tail:
        parts.append((tail, 0.0))
    if not parts:
        return [(text.strip(), 0.0)]
    return parts


def strip_pause_markers(text: str) -> str:
    """Remove pause markers and return plain text (for non-SSML path)."""
    return MARKER_PATTERN.sub(" ", text)

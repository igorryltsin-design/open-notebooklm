"""Shared TTS helpers."""


def voice_cfg_for_slot(voices: dict, slot: str) -> dict:
    """Найти конфиг голоса по имени слота: точное совпадение или без учёта регистра."""
    slot = (slot or "").strip()
    if not slot:
        return next(iter(voices.values()), {})
    if slot in voices:
        return voices[slot]
    slot_lower = slot.lower()
    for k, v in voices.items():
        if (k or "").strip().lower() == slot_lower:
            return v
    return next(iter(voices.values()), {})

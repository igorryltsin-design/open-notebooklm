from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _install_import_stubs() -> None:
    """Allow importing podcast_service without heavy RAG runtime deps."""
    if "chromadb" not in sys.modules:
        chromadb = types.ModuleType("chromadb")

        class HttpClient:  # pragma: no cover - import stub only
            pass

        chromadb.HttpClient = HttpClient
        sys.modules["chromadb"] = chromadb

    if "sentence_transformers" not in sys.modules:
        st = types.ModuleType("sentence_transformers")

        class SentenceTransformer:  # pragma: no cover - import stub only
            def __init__(self, *args, **kwargs):
                pass

        st.SentenceTransformer = SentenceTransformer
        sys.modules["sentence_transformers"] = st


_install_import_stubs()

from app.models import DialogueLine  # noqa: E402
from app.services import podcast_service  # noqa: E402


def _long_ru_text(seed: str) -> str:
    return (
        f"{seed} Этот фрагмент нужен для проверки устойчивости валидации turn taking и содержит "
        "достаточно слов чтобы пройти минимальные пороги по длине, символам и общему объему текста."
    )


class TurnTakingJsonStabilityTests(unittest.TestCase):
    def test_parse_script_json_repairs_object_sequence_with_russian_keys(self):
        voices = ["host", "guest1", "guest2"]
        raw = """
        Вот результат в JSON:
        {“спикер”: “ведущий”, “реплика”: “Добрый день! Начинаем разбор документа.”}
        {“роль”: “guest1”, “текст”: “Спасибо, давайте сначала определим основные термины.”}
        """
        lines = podcast_service._parse_script_json(raw, voices)
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0].voice, "host")
        self.assertEqual(lines[1].voice, "guest1")
        self.assertIn("разбор документа", lines[0].text)

    def test_parse_script_json_maps_transliterated_names_to_allowed_voices(self):
        voices = ["Игорь", "Аня", "Максим"]
        raw = """
        [
          {"speaker": "Igor", "text": "Вступление и рамка обсуждения."},
          {"speaker": "Anya", "text": "Разберем ключевые тезисы и детали."},
          {"speaker": "Maksim", "text": "Добавлю практический пример применения."}
        ]
        """
        lines = podcast_service._parse_script_json(raw, voices)
        self.assertEqual([ln.voice for ln in lines], voices)

    def test_parse_script_json_strips_markdown_fence_and_parses_array(self):
        """LLM often returns script wrapped in ```json ... ```; we must strip and parse."""
        voices = ["Игорь", "Аня", "Максим"]
        raw = """```json
[
  {"voice": "Игорь", "text": "Привет всем! Сегодня поговорим об отчёте по стеганографии."},
  {"voice": "Аня", "text": "В отчёте указано: PSNR высокий, SSIM почти 1."},
  {"voice": "Максим", "text": "Оценка риска Medium из-за умеренной загрузки LSB."}
]
```"""
        lines = podcast_service._parse_script_json(raw, voices)
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[0].voice, "Игорь")
        self.assertIn("стеганографии", lines[0].text)
        self.assertEqual(lines[1].voice, "Аня")
        self.assertIn("PSNR", lines[1].text)
        self.assertEqual(lines[2].voice, "Максим")
        self.assertIn("Medium", lines[2].text)

    def test_parse_turn_outline_json_normalizes_roles_and_scales_turns(self):
        voices = ["host", "guest1", "guest2"]
        raw = """
        ```json
        {
          "episode_goal": "Понять тему и вывести практические рекомендации",
          "blocks": [
            {
              "title": "Вступление",
              "goal": "Представить участников и тему",
              "role_order": ["HOST", "Гость 1"],
              "target_turns": 2
            },
            {
              "title": "Разбор кейсов",
              "instruction": "Сравнить два подхода по документу",
              "speakers": "guest2 | moderator | guest1",
              "target_turns": 5
            }
          ]
        }
        ```
        """
        outline = podcast_service._parse_turn_outline_json(
            raw,
            voices=voices,
            total_turns=8,
            scenario_key="debate",
        )
        self.assertEqual(outline.get("scenario"), "debate")
        blocks = outline.get("blocks") or []
        self.assertGreaterEqual(len(blocks), 2)
        # Intro block role order is forced to UI voices order by normalizer.
        self.assertEqual(blocks[0]["role_order"], voices)
        self.assertEqual(sum(int(b.get("target_turns", 0) or 0) for b in blocks), 8)
        for block in blocks:
            for role in block.get("role_order") or []:
                self.assertIn(role, voices)
        self.assertIn("Заверши обсуждение коротким итогом.", str(blocks[-1].get("instruction") or ""))

    def test_validate_script_completeness_turn_taking_rejects_single_speaker(self):
        lines = [
            DialogueLine(voice="host", text=_long_ru_text("Первая реплика.")),
            DialogueLine(voice="host", text=_long_ru_text("Вторая реплика.")),
        ]
        with self.assertRaisesRegex(ValueError, "одного спикера"):
            podcast_service.validate_script_completeness(
                lines,
                ["host", "guest1"],
                minutes=1,
                mode="turn_taking",
            )

    def test_validate_script_completeness_turn_taking_accepts_multi_speaker(self):
        lines = [
            DialogueLine(voice="host", text=_long_ru_text("Первая реплика ведущего.")),
            DialogueLine(voice="guest1", text=_long_ru_text("Ответ гостя с деталями.")),
        ]
        # Should not raise.
        podcast_service.validate_script_completeness(
            lines,
            ["host", "guest1"],
            minutes=1,
            mode="turn_taking",
        )


if __name__ == "__main__":
    unittest.main()


from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _install_import_stubs() -> None:
    if "pptx" not in sys.modules:
        pptx = types.ModuleType("pptx")

        class Presentation:  # pragma: no cover - import stub only
            def __init__(self, *args, **kwargs):
                self.slides = []

        pptx.Presentation = Presentation
        sys.modules["pptx"] = pptx


_install_import_stubs()

from app.services import ingest_service  # noqa: E402


class IngestCleanupTests(unittest.TestCase):
    def test_strip_repeated_pdf_margin_noise_removes_headers_and_page_numbers(self):
        pages = [
            "Компания Ромашка — Отчет 2025\n1. Введение\nТекст первой страницы.\n1",
            "Компания Ромашка — Отчет 2025\n2. Методика\nТекст второй страницы.\n2",
            "Компания Ромашка — Отчет 2025\n3. Результаты\nТекст третьей страницы.\n3",
        ]
        cleaned, removed = ingest_service._strip_repeated_pdf_margin_noise(pages)
        self.assertGreaterEqual(removed, 3)
        self.assertEqual(len(cleaned), 3)
        for idx, page in enumerate(cleaned, start=1):
            self.assertNotIn("Компания Ромашка", page)
            self.assertNotIn(f"\n{idx}\n", f"\n{page}\n")
            self.assertIn("Текст", page)

    def test_annotate_heading_markers_adds_markdown_heading_for_title_like_line(self):
        src = "Введение в систему\n\nЭто первый абзац с описанием."
        out = ingest_service._annotate_heading_markers(src)
        self.assertIn("## Введение в систему", out)
        self.assertIn("Это первый абзац", out)

    def test_inject_section_anchor_markers_adds_anchor_before_markdown_heading(self):
        src = "## Введение\nТекст раздела\n\n### Детали\nЕщё текст"
        out = ingest_service._inject_section_anchor_markers(src, scope_prefix="pdf:p2")
        self.assertIn("Якорь: pdf:p2:sec:1\n## Введение", out)
        self.assertIn("Якорь: pdf:p2:sec:2\n### Детали", out)
        self.assertEqual(out.count("Якорь: pdf:p2:sec:"), 2)

    def test_docx_heading_level_parses_english_and_russian_styles(self):
        self.assertEqual(ingest_service._docx_heading_level("Heading 2"), 2)
        self.assertEqual(ingest_service._docx_heading_level("Заголовок 3"), 3)
        self.assertIsNone(ingest_service._docx_heading_level("Normal"))

    def test_serialize_pdf_table_formats_marker_header_and_rows(self):
        table = [
            ["Параметр", "Значение", "Ед."],
            ["Температура", "25", "C"],
            ["Давление", "101.3", "кПа"],
        ]
        block = ingest_service._serialize_pdf_table(
            table,
            page_idx=2,
            table_idx=1,
            page_text="",
            caption="Таблица 1. Параметры среды",
        )
        self.assertIn("[PDF page 2]", block)
        self.assertIn("[PDF table 1]", block)
        self.assertIn("Якорь: pdf:p2:table:1", block)
        self.assertIn("## Таблица 1 (страница 2)", block)
        self.assertIn("Подпись: Таблица 1. Параметры среды", block)
        self.assertIn("Колонки: Параметр | Значение | Ед.", block)
        self.assertIn("Строка 1: Температура | 25 | C", block)
        self.assertIn("Строка 2: Давление | 101.3 | кПа", block)

    def test_serialize_pdf_table_skips_degenerate_or_redundant_tables(self):
        degenerate = [["Только одна ячейка"]]
        self.assertEqual(
            ingest_service._serialize_pdf_table(degenerate, page_idx=1, table_idx=1, page_text=""),
            "",
        )
        table = [
            ["Показатель", "Значение"],
            ["Метрика A", "10"],
            ["Метрика B", "20"],
        ]
        page_text = "Показатель Значение Метрика A 10 Метрика B 20"
        self.assertEqual(
            ingest_service._serialize_pdf_table(table, page_idx=1, table_idx=1, page_text=page_text),
            "",
        )

    def test_serialize_pptx_table_formats_marker_header_and_rows(self):
        table = [
            ["Метрика", "Значение"],
            ["MAE", "0.12"],
            ["RMSE", "0.21"],
        ]
        block = ingest_service._serialize_pptx_table(
            table,
            slide_idx=4,
            table_idx=2,
            caption="Таблица 2. Метрики модели",
        )
        self.assertIn("Слайд 4", block)
        self.assertIn("[PPTX table 2]", block)
        self.assertIn("Якорь: pptx:s4:table:2", block)
        self.assertIn("Подпись: Таблица 2. Метрики модели", block)
        self.assertIn("Колонки: Метрика | Значение", block)
        self.assertIn("Строка 1: MAE | 0.12", block)

    def test_pick_table_caption_prefers_matching_number_and_tracks_used(self):
        page_text = "\n".join([
            "Таблица 2. Основные метрики",
            "Какой-то текст",
            "Таблица 1. Параметры эксперимента",
        ])
        used = set()
        cap1 = ingest_service._pick_table_caption(page_text, 1, used=used)
        cap2 = ingest_service._pick_table_caption(page_text, 2, used=used)
        self.assertEqual(cap1, "Таблица 1. Параметры эксперимента")
        self.assertEqual(cap2, "Таблица 2. Основные метрики")

    def test_extract_pdf_table_blocks_dedupes_identical_tables_and_attaches_caption(self):
        table = [
            ["Колонка", "Значение"],
            ["A", "10"],
            ["B", "20"],
        ]

        class _FakePage:
            def extract_tables(self):
                return [table, table]

        blocks = ingest_service._extract_pdf_table_blocks(
            _FakePage(),
            page_idx=3,
            page_text="Таблица 1. Результаты измерений\nПрочий текст страницы",
        )
        self.assertEqual(len(blocks), 1)
        self.assertIn("Подпись: Таблица 1. Результаты измерений", blocks[0])
        self.assertIn("Якорь: pdf:p3:table:1", blocks[0])

    def test_extract_pdf_figure_caption_blocks_adds_anchors(self):
        page_text = "\n".join([
            "Рисунок 1. Архитектура системы",
            "Какой-то текст",
            "Fig. 2. Pipeline overview",
        ])
        blocks = ingest_service._extract_pdf_figure_caption_blocks(page_text, page_idx=5)
        self.assertEqual(len(blocks), 2)
        self.assertIn("[PDF figure 1]", blocks[0])
        self.assertIn("Якорь: pdf:p5:fig:1", blocks[0])
        self.assertIn("Подпись: Рисунок 1. Архитектура системы", blocks[0])
        self.assertIn("[PDF figure 2]", blocks[1])
        self.assertIn("Якорь: pdf:p5:fig:2", blocks[1])
        self.assertIn("Подпись: Fig. 2. Pipeline overview", blocks[1])

    def test_strip_caption_lines_from_text_block_removes_emitted_caption_duplicates(self):
        text = "\n".join([
            "Введение",
            "Таблица 1. Основные метрики",
            "Описание таблицы и выводы.",
            "Рисунок 2. Архитектура системы",
        ])
        stripped = ingest_service._strip_caption_lines_from_text_block(
            text,
            ["Таблица 1. Основные метрики", "Рисунок 2. Архитектура системы"],
        )
        self.assertIn("Введение", stripped)
        self.assertIn("Описание таблицы", stripped)
        self.assertNotIn("Таблица 1. Основные метрики", stripped)
        self.assertNotIn("Рисунок 2. Архитектура системы", stripped)

    def test_parse_pptx_includes_table_blocks_without_duplicating_table_shape_text(self):
        class _Cell:
            def __init__(self, text):
                self.text = text

        class _Row:
            def __init__(self, cells):
                self.cells = [_Cell(c) for c in cells]

        class _Table:
            def __init__(self, rows):
                self.rows = [_Row(r) for r in rows]

        class _Shape:
            def __init__(self, *, text=None, table=None, children=None):
                self.text = text
                self.has_table = table is not None
                self.table = _Table(table) if table is not None else None
                self.shapes = list(children or [])

        class _Slide:
            def __init__(self, shapes):
                self.shapes = shapes

        class _Presentation:
            def __init__(self, *args, **kwargs):
                self.slides = [
                    _Slide(
                        [
                            _Shape(text="Заголовок слайда"),
                            _Shape(text="Рисунок 1. Общая схема"),
                            _Shape(text="Таблица 1. Параметры"),
                            _Shape(text="Заголовок слайда"),
                            _Shape(
                                table=[
                                    ["Параметр", "Значение"],
                                    ["Скорость", "42"],
                                ]
                            ),
                            _Shape(children=[_Shape(text="Комментарий в группе")]),
                        ]
                    )
                ]

        with mock.patch.object(ingest_service, "Presentation", _Presentation):
            text = ingest_service._parse_pptx(Path("/tmp/fake.pptx"))
        self.assertIn("Слайд 1", text)
        self.assertIn("Заголовок слайда", text)
        self.assertEqual(text.count("Заголовок слайда"), 1)
        self.assertIn("Якорь: pptx:s1:sec:1", text)
        self.assertIn("Комментарий в группе", text)
        self.assertIn("[PPTX table 1]", text)
        self.assertIn("Якорь: pptx:s1:table:1", text)
        self.assertIn("Подпись: Таблица 1. Параметры", text)
        self.assertIn("[PPTX figure 1]", text)
        self.assertIn("Якорь: pptx:s1:fig:1", text)
        self.assertIn("Подпись: Рисунок 1. Общая схема", text)
        self.assertEqual(text.count("Таблица 1. Параметры"), 1)
        self.assertEqual(text.count("Рисунок 1. Общая схема"), 1)
        self.assertIn("Колонки: Параметр | Значение", text)
        self.assertIn("Строка 1: Скорость | 42", text)


if __name__ == "__main__":
    unittest.main()

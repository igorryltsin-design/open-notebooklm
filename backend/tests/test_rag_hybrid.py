from __future__ import annotations

import sys
import types
import unittest
from unittest import mock


def _install_import_stubs() -> None:
    """Allow importing rag_service without heavy runtime deps."""
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

from app.services import rag_service  # noqa: E402


class RagHybridTests(unittest.TestCase):
    def test_infer_chunk_metadata_from_pdf_marker_and_heading(self):
        chunk = "[PDF page 7]\n2.1 Методика эксперимента\nОписание процедуры и результатов."
        meta = rag_service._infer_chunk_metadata(chunk)
        self.assertEqual(meta.get("page"), 7)
        self.assertEqual(meta.get("section_path"), "2.1 Методика эксперимента")

    def test_infer_chunk_metadata_from_slide(self):
        chunk = "Слайд 4\nКлючевые показатели\nPrecision Recall F1"
        meta = rag_service._infer_chunk_metadata(chunk)
        self.assertEqual(meta.get("section_path"), "Слайд 4")

    def test_infer_chunk_metadata_from_structured_table_and_figure_markers(self):
        table_chunk = (
            "[PDF page 3]\n[PDF table 1]\n"
            "Якорь: pdf:p3:table:1\n"
            "## Таблица 1 (страница 3)\n"
            "Подпись: Таблица 1. Основные метрики\n"
            "Колонки: Метрика | Значение\n"
            "Строка 1: Accuracy | 0.91"
        )
        table_meta = rag_service._infer_chunk_metadata(table_chunk)
        self.assertEqual(table_meta.get("page"), 3)
        self.assertEqual(table_meta.get("source_type"), "pdf_table")
        self.assertEqual(table_meta.get("anchor"), "pdf:p3:table:1")
        self.assertEqual(table_meta.get("caption"), "Таблица 1. Основные метрики")

        fig_chunk = (
            "Слайд 2\n[PPTX figure 1]\n"
            "Якорь: pptx:s2:fig:1\n"
            "## Рисунок 1 (слайд 2)\n"
            "Подпись: Рисунок 1. Архитектура решения"
        )
        fig_meta = rag_service._infer_chunk_metadata(fig_chunk)
        self.assertEqual(fig_meta.get("source_type"), "pptx_figure")
        self.assertEqual(fig_meta.get("anchor"), "pptx:s2:fig:1")
        self.assertEqual(fig_meta.get("caption"), "Рисунок 1. Архитектура решения")

        ocr_chunk = "[OCR PDF page 9]\nРазмытый фрагмент отсканированной таблицы."
        ocr_meta = rag_service._infer_chunk_metadata(ocr_chunk)
        self.assertEqual(ocr_meta.get("page"), 9)
        self.assertEqual(ocr_meta.get("source_type"), "ocr_pdf")

    def test_hybrid_rerank_merges_vector_and_lexical_and_prefers_relevant_match(self):
        query = "методика эксперимента"
        vector_rows = [
            {
                "chunk_id": "doc_a_1",
                "text": "[PDF page 2]\n2.1 Методика эксперимента\nПодробное описание эксперимента.",
                "score_vector": 0.71,
                "chunk_index": 1,
                "page": 2,
                "section_path": "2.1 Методика эксперимента",
                "meta": {"page": 2, "chunk_index": 1},
            },
            {
                "chunk_id": "doc_a_2",
                "text": "Общий обзор документа без нужных терминов.",
                "score_vector": 0.69,
                "chunk_index": 2,
                "meta": {"chunk_index": 2},
            },
        ]
        lexical_rows = [
            {
                "chunk_id": "doc_a_1",
                "text": "[PDF page 2]\n2.1 Методика эксперимента\nПодробное описание эксперимента.",
                "score_lexical_raw": 1.42,
                "chunk_index": 1,
                "page": 2,
                "section_path": "2.1 Методика эксперимента",
                "meta": {"page": 2, "chunk_index": 1},
            },
            {
                "chunk_id": "doc_a_3",
                "text": "Методика измерений и дизайн эксперимента в приложении.",
                "score_lexical_raw": 1.05,
                "chunk_index": 3,
                "meta": {"chunk_index": 3},
            },
        ]

        ranked = rag_service._hybrid_rerank(query, vector_rows, lexical_rows, top_k=3)
        self.assertGreaterEqual(len(ranked), 2)
        self.assertEqual(ranked[0]["chunk_id"], "doc_a_1")
        self.assertIn("score", ranked[0])
        self.assertEqual(ranked[0].get("page"), 2)
        self.assertEqual(ranked[0].get("section_path"), "2.1 Методика эксперимента")

    def test_retrieve_fallback_keeps_contract_when_hybrid_fails(self):
        vector_rows = [
            {
                "chunk_id": "doc_x_0",
                "text": "Фрагмент",
                "score_vector": 0.55,
                "chunk_index": 0,
                "page": 1,
                "section_path": "Введение",
                "anchor": "pdf:p1:fig:1",
                "caption": "Рисунок 1. Введение",
                "source_type": "pdf_figure",
                "meta": {"page": 1, "chunk_index": 0},
            }
        ]
        with mock.patch.object(rag_service, "_vector_retrieve", return_value=vector_rows), \
             mock.patch.object(rag_service, "_lexical_retrieve", return_value=[]), \
             mock.patch.object(rag_service, "_hybrid_rerank", side_effect=RuntimeError("boom")):
            out = rag_service.retrieve("doc_x", "вопрос", top_k=1)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["chunk_id"], "doc_x_0")
        self.assertEqual(out[0]["chunk_index"], 0)
        self.assertEqual(out[0]["page"], 1)
        self.assertEqual(out[0]["section_path"], "Введение")
        self.assertEqual(out[0]["anchor"], "pdf:p1:fig:1")
        self.assertEqual(out[0]["caption"], "Рисунок 1. Введение")
        self.assertEqual(out[0]["source_type"], "pdf_figure")
        self.assertEqual((out[0].get("source_locator") or {}).get("kind"), "pdf")
        self.assertEqual((out[0].get("source_locator") or {}).get("page"), 1)
        self.assertIn("score", out[0])

    def test_build_source_locator_uses_anchor_and_quote_span(self):
        locator = rag_service.build_source_locator(
            chunk_id="doc_1",
            chunk_index=1,
            text="[PDF page 4]\\n## Методика\\nВажная деталь эксперимента и выводы.",
            page=None,
            section_path="Методика",
            anchor="pdf:p4:sec:2",
            caption=None,
            source_type="pdf_table",
            highlight_hint="деталь эксперимента",
        )
        self.assertEqual(locator.get("kind"), "pdf")
        self.assertEqual(locator.get("page"), 4)
        self.assertEqual(locator.get("anchor"), "pdf:p4:sec:2")
        self.assertIsInstance(locator.get("char_start"), int)
        self.assertIsInstance(locator.get("char_end"), int)


if __name__ == "__main__":
    unittest.main()

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
    if "chromadb" not in sys.modules:
        chromadb = types.ModuleType("chromadb")
        class HttpClient:
            def __init__(self, *args, **kwargs):
                pass
        chromadb.HttpClient = HttpClient
        sys.modules["chromadb"] = chromadb
    if "sentence_transformers" not in sys.modules:
        st = types.ModuleType("sentence_transformers")
        class SentenceTransformer:
            def __init__(self, *args, **kwargs):
                pass
            def encode(self, texts, **kwargs):
                if isinstance(texts, str):
                    return [0.0]
                return [[0.0] for _ in texts]
        st.SentenceTransformer = SentenceTransformer
        sys.modules["sentence_transformers"] = st


_install_import_stubs()

from app.services import podcast_service  # noqa: E402


class KnowledgeModeTests(unittest.TestCase):
    def test_normalize_knowledge_mode_defaults_to_document_only(self):
        self.assertEqual(podcast_service._normalize_knowledge_mode(None), "document_only")
        self.assertEqual(podcast_service._normalize_knowledge_mode("bad-value"), "document_only")
        self.assertEqual(podcast_service._normalize_knowledge_mode("hybrid_model"), "hybrid_model")

    def test_script_knowledge_mode_degrades_for_unsupported_scenario(self):
        self.assertEqual(
            podcast_service._effective_script_knowledge_mode("classic_overview", "hybrid_model"),
            "document_only",
        )
        self.assertEqual(
            podcast_service._effective_script_knowledge_mode("critique", "hybrid_model"),
            "hybrid_model",
        )

    def test_build_qa_payload_includes_hybrid_instruction(self):
        ctx = [{
            "document_id": "doc-1",
            "chunk_id": "c-1",
            "score": 0.91,
            "text": "Подтвержденный фрагмент документа.",
            "matched_terms": ["подтвержденный"],
            "source_locator": {"char_start": 0, "char_end": 24},
        }]
        with (
            mock.patch.object(podcast_service, "_qa_context", return_value=ctx),
            mock.patch.object(podcast_service, "_qa_summary_context", return_value=""),
            mock.patch.object(podcast_service, "_build_citation_payload", return_value={"document_id": "doc-1", "chunk_id": "c-1"}),
        ):
            system, _user, _citations, _conf, _breakdown, effective = podcast_service.build_qa_payload(
                ["doc-1"],
                "Что улучшить?",
                knowledge_mode="hybrid_model",
            )
        self.assertEqual(effective, "hybrid_model")
        self.assertIn("Вне документа:", system)


if __name__ == "__main__":
    unittest.main()

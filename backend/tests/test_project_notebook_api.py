from __future__ import annotations

import sys
import tempfile
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

    if "chromadb" not in sys.modules:
        chromadb = types.ModuleType("chromadb")

        class HttpClient:  # pragma: no cover - import stub only
            def __init__(self, *args, **kwargs):
                pass

        chromadb.HttpClient = HttpClient
        sys.modules["chromadb"] = chromadb

    if "sentence_transformers" not in sys.modules:
        st = types.ModuleType("sentence_transformers")

        class SentenceTransformer:  # pragma: no cover - import stub only
            def __init__(self, *args, **kwargs):
                pass

            def encode(self, texts, **kwargs):
                if isinstance(texts, str):
                    return [0.0]
                return [[0.0] for _ in texts]

        st.SentenceTransformer = SentenceTransformer
        sys.modules["sentence_transformers"] = st

    if "app.tts.dispatcher" not in sys.modules:
        tts_dispatcher = types.ModuleType("app.tts.dispatcher")

        async def _stub_synthesise_script(*args, **kwargs):  # pragma: no cover - import stub only
            raise RuntimeError("stub tts dispatcher should be patched in test")

        tts_dispatcher.synthesise_script = _stub_synthesise_script
        sys.modules["app.tts.dispatcher"] = tts_dispatcher


_install_import_stubs()

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app import project_store  # noqa: E402


class ProjectNotebookApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir_obj = tempfile.TemporaryDirectory(prefix="project_notebook_api_")
        self.tmpdir = Path(self.tmpdir_obj.name)
        self.projects_file = self.tmpdir / "projects.json"
        self.patch = mock.patch.object(project_store, "PROJECTS_FILE", self.projects_file)
        self.patch.start()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        try:
            self.client.close()
        except Exception:
            pass
        self.patch.stop()
        self.tmpdir_obj.cleanup()

    def test_project_notes_and_pins_lifecycle(self):
        create = self.client.post("/api/projects", json={"name": "Подборка A", "document_ids": ["doc-1", "doc-2"]})
        self.assertEqual(create.status_code, 200, create.text)
        project_id = str(create.json()["project_id"])

        notebook = self.client.get(f"/api/projects/{project_id}/notebook")
        self.assertEqual(notebook.status_code, 200, notebook.text)
        self.assertEqual(notebook.json().get("notes"), "")
        self.assertEqual(notebook.json().get("pinned_qas"), [])

        notes = self.client.put(
            f"/api/projects/{project_id}/notes",
            json={"notes": "Ключевые выводы:\n1) Сфокусироваться на таблицах"},
        )
        self.assertEqual(notes.status_code, 200, notes.text)
        self.assertIn("Ключевые выводы", notes.json().get("notes", ""))

        add_pin = self.client.post(
            f"/api/projects/{project_id}/pins",
            json={
                "question": "О чём документы?",
                "answer": "Про автоматизацию грузовой доставки.",
                "mode": "qa",
                "meta": "Надежность: 74%",
                "citations": [{"document_id": "doc-1", "chunk_id": "ch-2", "page": 4}],
            },
        )
        self.assertEqual(add_pin.status_code, 200, add_pin.text)
        pin_json = add_pin.json()
        self.assertTrue(str(pin_json.get("pin_id") or "").strip())
        self.assertEqual(pin_json.get("question"), "О чём документы?")

        notebook_after = self.client.get(f"/api/projects/{project_id}/notebook")
        self.assertEqual(notebook_after.status_code, 200, notebook_after.text)
        self.assertEqual(len(notebook_after.json().get("pinned_qas") or []), 1)

        pin_id = str(notebook_after.json()["pinned_qas"][0]["pin_id"])
        delete_pin = self.client.delete(f"/api/projects/{project_id}/pins/{pin_id}")
        self.assertEqual(delete_pin.status_code, 200, delete_pin.text)
        self.assertTrue(delete_pin.json().get("ok"))

        notebook_final = self.client.get(f"/api/projects/{project_id}/notebook")
        self.assertEqual(notebook_final.status_code, 200, notebook_final.text)
        self.assertEqual(notebook_final.json().get("pinned_qas"), [])

    def test_add_pin_rejects_empty_answer(self):
        create = self.client.post("/api/projects", json={"name": "Подборка B", "document_ids": []})
        self.assertEqual(create.status_code, 200, create.text)
        project_id = str(create.json()["project_id"])
        bad_pin = self.client.post(
            f"/api/projects/{project_id}/pins",
            json={"question": "Q", "answer": "   "},
        )
        self.assertEqual(bad_pin.status_code, 400, bad_pin.text)
        self.assertIn("пустой ответ", str(bad_pin.text).lower())

    def test_project_settings_roundtrip(self):
        create = self.client.post("/api/projects", json={"name": "Подборка C", "document_ids": ["doc-1"]})
        self.assertEqual(create.status_code, 200, create.text)
        project_id = str(create.json()["project_id"])

        get_default = self.client.get(f"/api/projects/{project_id}/settings")
        self.assertEqual(get_default.status_code, 200, get_default.text)
        default_settings = get_default.json().get("settings") or {}
        self.assertEqual(default_settings.get("chat", {}).get("question_mode"), "default")
        self.assertEqual(default_settings.get("script", {}).get("minutes"), 5)

        updated = self.client.put(
            f"/api/projects/{project_id}/settings",
            json={
                "settings": {
                    "chat": {
                        "strict_sources": False,
                        "question_mode": "quote",
                        "answer_length": "short",
                        "scope": "collection",
                    },
                    "script": {
                        "minutes": 12,
                        "style": "debate",
                        "scenario": "interview",
                        "generation_mode": "turn_taking",
                        "focus": "Практические кейсы",
                        "tts_friendly": False,
                    },
                }
            },
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        updated_settings = updated.json().get("settings") or {}
        self.assertEqual(updated_settings.get("chat", {}).get("question_mode"), "quote")
        self.assertEqual(updated_settings.get("chat", {}).get("scope"), "collection")
        self.assertEqual(updated_settings.get("script", {}).get("minutes"), 12)
        self.assertEqual(updated_settings.get("script", {}).get("generation_mode"), "turn_taking")

        project = self.client.get(f"/api/projects/{project_id}")
        self.assertEqual(project.status_code, 200, project.text)
        self.assertEqual(project.json().get("settings", {}).get("script", {}).get("style"), "debate")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import asyncio
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _install_import_stubs() -> None:
    """Allow importing app.main/api without heavy optional runtime deps."""
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

    # Avoid importing heavy TTS engines (torchaudio/silero/piper stack) in tests.
    if "app.tts.dispatcher" not in sys.modules:
        tts_dispatcher = types.ModuleType("app.tts.dispatcher")

        async def _stub_synthesise_script(*args, **kwargs):  # pragma: no cover - replaced by mocks in tests
            raise RuntimeError("stub tts dispatcher should be patched in test")

        tts_dispatcher.synthesise_script = _stub_synthesise_script
        sys.modules["app.tts.dispatcher"] = tts_dispatcher


_install_import_stubs()

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.models import DialogueLine, SourceFragment  # noqa: E402
from app.routers import api  # noqa: E402
from app import document_store, job_manager  # noqa: E402


class ApiSmokePipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir_obj = tempfile.TemporaryDirectory(prefix="api_smoke_")
        self.tmpdir = Path(self.tmpdir_obj.name)
        self.inputs_dir = self.tmpdir / "inputs"
        self.outputs_dir = self.tmpdir / "outputs"
        self.data_dir = self.tmpdir / "data"
        self.inputs_dir.mkdir(parents=True, exist_ok=True)
        self.outputs_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.docs_file = self.data_dir / "documents.json"
        self.jobs_file = self.data_dir / "jobs.json"
        self.jobs_artifacts_file = self.data_dir / "jobs_artifacts.json"

        self.patches = [
            mock.patch.object(api, "INPUTS_DIR", self.inputs_dir),
            mock.patch.object(api, "OUTPUTS_DIR", self.outputs_dir),
            mock.patch.object(document_store, "DOCUMENTS_FILE", self.docs_file),
            mock.patch.object(job_manager, "JOBS_FILE", self.jobs_file),
            mock.patch.object(job_manager, "JOB_ARTIFACTS_FILE", self.jobs_artifacts_file),
        ]
        for p in self.patches:
            p.start()

        api._texts.clear()
        api._scripts.clear()
        api._script_meta.clear()
        document_store.clear_all_documents()
        asyncio.run(job_manager.clear_all_jobs())
        job_manager._lane_semaphores.clear()
        job_manager._cancel_events.clear()
        job_manager._job_tasks.clear()

        self.client = TestClient(app)

    def tearDown(self) -> None:
        try:
            self.client.close()
        except Exception:
            pass
        api._texts.clear()
        api._scripts.clear()
        api._script_meta.clear()
        for p in reversed(self.patches):
            p.stop()
        self.tmpdir_obj.cleanup()

    def test_smoke_pipeline_upload_ingest_summary_script_audio_job(self):
        async def _fake_tts(script, document_id, progress_cb=None, apply_music=True, apply_postprocess=True):
            self.assertTrue(isinstance(script, list) and len(script) > 0)
            self.assertEqual(document_id, self.document_id)
            if progress_cb:
                await progress_cb(35)
                await progress_cb(70)
            out = self.outputs_dir / f"{document_id}_podcast.mp3"
            out.write_bytes(b"ID3\x00fake-mp3")
            return out

        with (
            mock.patch.object(api.ingest_service, "parse_file", return_value="Введение\nТекст документа для smoke test."),
            mock.patch.object(api.rag_service, "chunk_text", return_value=["[PDF page 1]\n# Введение\nТекст документа для smoke test."]),
            mock.patch.object(api.rag_service, "index_document", return_value=1),
            mock.patch.object(
                api.rag_service,
                "get_chunk",
                return_value={
                    "chunk_id": "stub_0",
                    "chunk_index": 0,
                    "text": "[PDF page 1]\n# Введение\nТекст документа для smoke test.",
                    "page": 1,
                    "section_path": "Введение",
                    "anchor": "pdf:p1:sec:1",
                    "caption": None,
                    "source_type": "pdf_table",
                    "source_locator": {"kind": "pdf", "page": 1, "quote": "Текст документа для smoke test."},
                },
            ),
            mock.patch.object(
                api.podcast_service,
                "generate_summary",
                new=mock.AsyncMock(
                    return_value=(
                        "Краткое саммари документа.",
                        [SourceFragment(chunk_id="doc_chunk_1", text="Текст документа для smoke test.")],
                    )
                ),
            ),
            mock.patch.object(
                api.podcast_service,
                "generate_podcast_script",
                new=mock.AsyncMock(
                    return_value=[
                        DialogueLine(voice="host", text="Здравствуйте, это тестовый выпуск."),
                        DialogueLine(voice="guest1", text="Подтверждаю, smoke-пайплайн работает."),
                    ]
                ),
            ),
            mock.patch.object(api, "tts_synthesise_script", side_effect=_fake_tts),
        ):
            # 1) upload
            upload = self.client.post(
                "/api/upload",
                files={"file": ("smoke.txt", b"dummy input text", "text/plain")},
            )
            self.assertEqual(upload.status_code, 200, upload.text)
            up_json = upload.json()
            self.document_id = str(up_json["document_id"])
            self.assertEqual(up_json["filename"], "smoke.txt")
            self.assertFalse(up_json.get("duplicate", False))
            document_store.update_document(self.document_id, file_hash="")

            duplicate_upload = self.client.post(
                "/api/upload",
                files={"file": ("smoke-copy.txt", b"dummy input text", "text/plain")},
            )
            self.assertEqual(duplicate_upload.status_code, 200, duplicate_upload.text)
            duplicate_json = duplicate_upload.json()
            self.assertTrue(duplicate_json.get("duplicate"))
            self.assertEqual(str(duplicate_json["document_id"]), self.document_id)
            self.assertEqual(str(duplicate_json.get("duplicate_of") or ""), self.document_id)

            # 2) ingest
            ingest = self.client.post(f"/api/ingest/{self.document_id}")
            self.assertEqual(ingest.status_code, 200, ingest.text)
            self.assertEqual(ingest.json()["chunks"], 1)

            # 2.1) source file and chunk locator endpoints
            source_file = self.client.get(f"/api/documents/{self.document_id}/source")
            self.assertEqual(source_file.status_code, 200, source_file.text)
            chunk_locator = self.client.get(f"/api/documents/{self.document_id}/chunks/{self.document_id}_0")
            self.assertEqual(chunk_locator.status_code, 200, chunk_locator.text)
            chunk_json = chunk_locator.json()
            self.assertEqual(chunk_json.get("document_id"), self.document_id)
            self.assertTrue(str(chunk_json.get("source_url") or "").endswith(f"/api/documents/{self.document_id}/source"))
            self.assertEqual((chunk_json.get("source_locator") or {}).get("kind"), "pdf")

            # 3) summary
            summary = self.client.get(f"/api/summary/{self.document_id}")
            self.assertEqual(summary.status_code, 200, summary.text)
            summary_json = summary.json()
            self.assertEqual(summary_json["document_id"], self.document_id)
            self.assertIn("саммари", summary_json["summary"].lower())
            self.assertEqual(len(summary_json.get("sources") or []), 1)

            # 4) script
            script = self.client.post(
                f"/api/podcast_script/{self.document_id}",
                json={"minutes": 3, "style": "conversational", "voices": ["host", "guest1"]},
            )
            self.assertEqual(script.status_code, 200, script.text)
            script_json = script.json()
            self.assertEqual(script_json["document_id"], self.document_id)
            self.assertEqual(len(script_json.get("script") or []), 2)

            # 4.1) script versions lifecycle: list -> import (new version) -> compare -> restore
            versions_before = self.client.get(f"/api/podcast_script/{self.document_id}/versions")
            self.assertEqual(versions_before.status_code, 200, versions_before.text)
            vb_json = versions_before.json()
            self.assertTrue((vb_json.get("versions") or []))
            v1_id = str(vb_json["versions"][0]["version_id"])

            edited_script = [
                {"voice": "host", "text": "Здравствуйте, это обновлённый тестовый выпуск."},
                {"voice": "guest1", "text": "Подтверждаю, версия скрипта изменилась."},
            ]
            imported = self.client.post(
                f"/api/podcast_script/{self.document_id}/import",
                json={"script": edited_script},
            )
            self.assertEqual(imported.status_code, 200, imported.text)
            self.assertEqual(imported.json()["script"][0]["text"], edited_script[0]["text"])

            versions_after = self.client.get(f"/api/podcast_script/{self.document_id}/versions")
            self.assertEqual(versions_after.status_code, 200, versions_after.text)
            va_json = versions_after.json()
            self.assertGreaterEqual(len(va_json.get("versions") or []), 2)
            v2_id = str(va_json["versions"][-1]["version_id"])

            compare = self.client.post(
                f"/api/podcast_script/{self.document_id}/versions/compare",
                json={"left_version_id": v1_id, "right_version_id": v2_id},
            )
            self.assertEqual(compare.status_code, 200, compare.text)
            diff = compare.json().get("diff") or {}
            self.assertGreaterEqual(int(diff.get("changed") or 0) + int(diff.get("added") or 0) + int(diff.get("removed") or 0), 1)

            restore = self.client.post(f"/api/podcast_script/{self.document_id}/versions/{v1_id}/restore")
            self.assertEqual(restore.status_code, 200, restore.text)
            restored_script = restore.json().get("script") or []
            self.assertEqual(restored_script[0]["text"], "Здравствуйте, это тестовый выпуск.")

            # 4.1) script lock metadata (backend-persist locks)
            save_locks = self.client.post(
                f"/api/podcast_script/{self.document_id}/locks",
                json={"locks": [1, 0, 99, -1, 1]},
            )
            self.assertEqual(save_locks.status_code, 200, save_locks.text)
            self.assertEqual(save_locks.json()["locks"], [0, 1])
            get_locks = self.client.get(f"/api/podcast_script/{self.document_id}/locks")
            self.assertEqual(get_locks.status_code, 200, get_locks.text)
            self.assertEqual(get_locks.json()["locks"], [0, 1])

            # 5) audio job + 6) job status
            audio_job = self.client.post(f"/api/podcast_audio/{self.document_id}")
            self.assertEqual(audio_job.status_code, 200, audio_job.text)
            job_id = audio_job.json()["job_id"]

            status_json = None
            for _ in range(5):
                status_res = self.client.get(f"/api/jobs/{job_id}")
                self.assertEqual(status_res.status_code, 200, status_res.text)
                status_json = status_res.json()
                if status_json.get("status") == "done":
                    break
                time.sleep(0.05)
            self.assertIsNotNone(status_json)
            self.assertEqual(status_json["status"], "done", status_json)
            self.assertTrue(status_json.get("output_paths"))
            self.assertTrue(Path(status_json["output_paths"][0]).exists())
            self.assertEqual(status_json.get("lane"), "audio")
            self.assertGreaterEqual(int(status_json.get("lane_limit") or 0), 1)
            self.assertIsInstance(status_json.get("lane_running"), int)
            self.assertIsInstance(status_json.get("lane_pending"), int)
            self.assertTrue(self.jobs_artifacts_file.exists())

            retry = self.client.post(f"/api/jobs/{job_id}/retry")
            self.assertEqual(retry.status_code, 200, retry.text)
            retry_json = retry.json()
            retry_job_id = str(retry_json.get("job_id") or "")
            self.assertTrue(retry_job_id)
            self.assertEqual(str(retry_json.get("parent_job_id") or ""), job_id)

            parent_after_retry = self.client.get(f"/api/jobs/{job_id}")
            self.assertEqual(parent_after_retry.status_code, 200, parent_after_retry.text)
            self.assertEqual(parent_after_retry.json().get("status"), "retrying")

            # Persisted store smoke assertions.
            stored = document_store.get_document(self.document_id)
            self.assertIsNotNone(stored)
            self.assertTrue(stored.get("ingested"))
            self.assertEqual(stored.get("chunks"), 1)
            self.assertTrue(stored.get("summary"))
            self.assertTrue(isinstance(stored.get("script"), list) and len(stored.get("script")) == 2)
            self.assertEqual((stored.get("script_meta") or {}).get("locks"), [0, 1])

    def test_fulltext_endpoint_supports_window_ranges(self):
        document_id = "doc_windowed"
        document_store.add_document(document_id, "windowed.txt")
        quote = "ЦЕЛЕВОЙ ФРАГМЕНТ ДЛЯ ОКНА"
        text = ("Вступление и вводные данные.\n" * 8000) + quote + ("\nЗаключительный блок." * 7000)
        api._texts[document_id] = text
        target_start = text.index(quote)
        target_len = len(quote)
        anchor_id = f"a:{document_id}:chunk:o{target_start}:{target_len}"

        win_by_anchor = self.client.get(
            f"/api/documents/{document_id}/fulltext",
            params={"anchor_id": anchor_id, "max_chars": 4000},
        )
        self.assertEqual(win_by_anchor.status_code, 200, win_by_anchor.text)
        anchor_json = win_by_anchor.json()
        self.assertTrue(anchor_json.get("is_windowed"))
        self.assertTrue(anchor_json.get("has_more_before"))
        self.assertTrue(anchor_json.get("has_more_after"))
        self.assertIn(quote, str(anchor_json.get("text") or ""))
        self.assertLessEqual(int(anchor_json.get("start") or 0), target_start)
        self.assertGreaterEqual(int(anchor_json.get("end") or 0), target_start + target_len)

        win_by_highlight = self.client.get(
            f"/api/documents/{document_id}/fulltext",
            params={"highlight": quote, "max_chars": 4000},
        )
        self.assertEqual(win_by_highlight.status_code, 200, win_by_highlight.text)
        highlight_json = win_by_highlight.json()
        self.assertTrue(highlight_json.get("is_windowed"))
        self.assertIn(quote, str(highlight_json.get("text") or ""))

        full_res = self.client.get(
            f"/api/documents/{document_id}/fulltext",
            params={"full": 1, "max_chars": 4000},
        )
        self.assertEqual(full_res.status_code, 200, full_res.text)
        full_json = full_res.json()
        self.assertFalse(full_json.get("is_windowed"))
        self.assertEqual(int(full_json.get("start", -1)), 0)
        self.assertEqual(int(full_json.get("end", -1)), len(text))
        self.assertEqual(int(full_json.get("total_chars", -1)), len(text))
        self.assertEqual(str(full_json.get("text") or ""), text)


if __name__ == "__main__":
    unittest.main()

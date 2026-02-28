from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import job_manager  # noqa: E402
from app.models import JobStatus  # noqa: E402


class JobQueueVisibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir_obj = tempfile.TemporaryDirectory(prefix="job_queue_vis_")
        self.jobs_file = Path(self.tmpdir_obj.name) / "jobs.json"
        self.jobs_artifacts_file = Path(self.tmpdir_obj.name) / "jobs_artifacts.json"
        self.jobs_file.parent.mkdir(parents=True, exist_ok=True)
        self.jobs_patch = mock.patch.object(job_manager, "JOBS_FILE", self.jobs_file)
        self.artifacts_patch = mock.patch.object(job_manager, "JOB_ARTIFACTS_FILE", self.jobs_artifacts_file)
        self.jobs_patch.start()
        self.artifacts_patch.start()
        asyncio.run(job_manager.clear_all_jobs())
        job_manager._lane_semaphores.clear()
        job_manager._cancel_events.clear()
        job_manager._job_tasks.clear()

    def tearDown(self) -> None:
        asyncio.run(job_manager.clear_all_jobs())
        self.artifacts_patch.stop()
        self.jobs_patch.stop()
        self.tmpdir_obj.cleanup()

    def test_get_job_view_includes_lane_runtime_metrics_and_queue_position(self):
        audio1 = asyncio.run(job_manager.create_job_with_meta(lane="audio"))
        audio2 = asyncio.run(job_manager.create_job_with_meta(lane="audio"))
        batch1 = asyncio.run(job_manager.create_job_with_meta(lane="batch"))

        asyncio.run(job_manager.update_job(audio1, status=JobStatus.running, progress=25))
        asyncio.run(job_manager.update_job(batch1, status=JobStatus.pending, progress=1))

        pending_audio = asyncio.run(job_manager.get_job_view(audio2))
        self.assertIsNotNone(pending_audio)
        self.assertEqual(pending_audio.lane, "audio")
        self.assertGreaterEqual(int(pending_audio.lane_limit or 0), 1)
        self.assertEqual(pending_audio.lane_running, 1)
        self.assertEqual(pending_audio.lane_pending, 1)
        self.assertEqual(pending_audio.queue_position, 1)

        running_audio = asyncio.run(job_manager.get_job_view(audio1))
        self.assertIsNotNone(running_audio)
        self.assertEqual(running_audio.status, JobStatus.running)
        self.assertEqual(running_audio.lane_running, 1)
        self.assertEqual(running_audio.lane_pending, 1)
        self.assertIsNone(running_audio.queue_position)

        lanes = asyncio.run(job_manager.get_lane_stats())
        self.assertIn("audio", lanes)
        self.assertIn("batch", lanes)
        self.assertEqual(lanes["audio"]["running"], 1)
        self.assertEqual(lanes["audio"]["pending"], 1)
        self.assertEqual(lanes["batch"]["pending"], 1)

    def test_job_artifacts_are_persisted_separately_from_job_metadata(self):
        job_id = asyncio.run(job_manager.create_job_with_meta(lane="audio"))
        asyncio.run(
            job_manager.update_job(
                job_id,
                status=JobStatus.done,
                progress=100,
                output_paths=["/tmp/a.mp3", "/tmp/b.json"],
            )
        )
        self.assertTrue(self.jobs_file.exists())
        self.assertTrue(self.jobs_artifacts_file.exists())
        jobs_payload = self.jobs_file.read_text(encoding="utf-8")
        artifacts_payload = self.jobs_artifacts_file.read_text(encoding="utf-8")
        self.assertIn(f"\"{job_id}\"", jobs_payload)
        self.assertIn("\"output_paths\": []", jobs_payload)
        self.assertIn(f"\"{job_id}\"", artifacts_payload)
        self.assertIn("/tmp/a.mp3", artifacts_payload)


if __name__ == "__main__":
    unittest.main()

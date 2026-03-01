"""Async job manager with file-based registry."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Coroutine, Optional, Any

from app.config import (
    DATA_DIR,
    JOB_MAX_PARALLEL_AUDIO,
    JOB_MAX_PARALLEL_BATCH,
    JOB_MAX_PARALLEL_DEFAULT,
    JOB_MAX_PARALLEL_INGEST,
)
from app.models import JobInfo, JobStatus

logger = logging.getLogger(__name__)

JOBS_FILE = DATA_DIR / "jobs.json"
JOB_ARTIFACTS_FILE = DATA_DIR / "jobs_artifacts.json"
_lock = asyncio.Lock()
_lane_semaphores: dict[str, asyncio.Semaphore] = {}
_cancel_events: dict[str, asyncio.Event] = {}
_job_tasks: dict[str, asyncio.Task] = {}

# In-memory cache (authoritative); periodically flushed to disk.
_jobs: dict[str, JobInfo] = {}
_job_artifacts: dict[str, list[str]] = {}


class JobCancelledError(Exception):
    """Raised when a cooperative cancellation request was received for a job."""

    def __init__(self, job_id: str, message: str = "Задача отменена пользователем") -> None:
        super().__init__(message)
        self.job_id = job_id


def _max_parallel_for_lane(lane: str) -> int:
    key = (lane or "default").strip().lower()
    limits = {
        "default": JOB_MAX_PARALLEL_DEFAULT,
        "ingest": JOB_MAX_PARALLEL_INGEST,
        "audio": JOB_MAX_PARALLEL_AUDIO,
        "batch": JOB_MAX_PARALLEL_BATCH,
    }
    raw = limits.get(key, JOB_MAX_PARALLEL_DEFAULT)
    return max(1, int(raw))


def _semaphore_for_lane(lane: str) -> asyncio.Semaphore:
    key = (lane or "default").strip().lower() or "default"
    sem = _lane_semaphores.get(key)
    if sem is None:
        sem = asyncio.Semaphore(_max_parallel_for_lane(key))
        _lane_semaphores[key] = sem
    return sem


def _normalize_output_paths(paths) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in paths or []:
        value = str(raw or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _load_artifacts_from_disk() -> dict[str, list[str]]:
    if not JOB_ARTIFACTS_FILE.exists():
        return {}
    try:
        payload = json.loads(JOB_ARTIFACTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        str(job_id): _normalize_output_paths(paths)
        for job_id, paths in payload.items()
        if str(job_id).strip()
    }


def _flush_artifacts_to_disk() -> None:
    JOB_ARTIFACTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    JOB_ARTIFACTS_FILE.write_text(
        json.dumps(_job_artifacts, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _load_from_disk() -> None:
    global _jobs, _job_artifacts
    _jobs = {}
    _job_artifacts = _load_artifacts_from_disk()
    if JOBS_FILE.exists():
        try:
            data = json.loads(JOBS_FILE.read_text())
            for k, v in (data or {}).items():
                try:
                    job = JobInfo(**v)
                except Exception:
                    continue
                legacy_paths = _normalize_output_paths(getattr(job, "output_paths", []))
                if legacy_paths and not _job_artifacts.get(k):
                    _job_artifacts[k] = legacy_paths
                job.output_paths = _normalize_output_paths(_job_artifacts.get(k) or legacy_paths)
                _jobs[k] = job
        except Exception:
            _jobs = {}
    if _job_artifacts:
        _flush_artifacts_to_disk()


def _meta_row(job: JobInfo) -> dict[str, Any]:
    payload = job.model_dump()
    payload["output_paths"] = []
    return payload


def _recover_incomplete_jobs_on_startup() -> None:
    """Mark jobs left in non-terminal states by a previous backend process."""
    if not _jobs:
        return
    recovered_running = 0
    recovered_pending = 0
    recovered_retrying = 0
    for job in _jobs.values():
        if job.status == JobStatus.running:
            job.status = JobStatus.error
            job.error = "Задача прервана из-за перезапуска backend. Используйте Retry."
            job.cancel_requested = False
            job.progress = max(0, min(int(job.progress or 0), 99))
            recovered_running += 1
            continue
        if job.status == JobStatus.pending:
            job.status = JobStatus.cancelled
            job.error = "Очередь задач сброшена после перезапуска backend. Используйте Retry."
            job.cancel_requested = False
            job.progress = 0
            recovered_pending += 1
            continue
        if job.status == JobStatus.retrying:
            job.status = JobStatus.error
            job.error = "Retry был прерван из-за перезапуска backend. Запустите Retry снова."
            job.cancel_requested = False
            recovered_retrying += 1
    if recovered_running or recovered_pending or recovered_retrying:
        logger.warning(
            "Recovered stale jobs after restart: running->error=%d pending->cancelled=%d retrying->error=%d",
            recovered_running,
            recovered_pending,
            recovered_retrying,
        )
        _flush_to_disk()


def _flush_to_disk() -> None:
    JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    JOBS_FILE.write_text(
        json.dumps({k: _meta_row(v) for k, v in _jobs.items()}, indent=2),
        encoding="utf-8",
    )
    _flush_artifacts_to_disk()


# Init on import
_load_from_disk()
_recover_incomplete_jobs_on_startup()


async def create_job() -> str:
    """Register a new pending job and return its ID."""
    return await create_job_with_meta()


async def create_job_with_meta(
    *,
    lane: str = "default",
    job_type: str | None = None,
    recipe: dict[str, Any] | None = None,
    parent_job_id: str | None = None,
) -> str:
    """Register a new pending job and optionally persist retry metadata."""
    job_id = uuid.uuid4().hex[:12]
    lane_key = (lane or "default").strip().lower() or "default"
    async with _lock:
        _jobs[job_id] = JobInfo(
            job_id=job_id,
            status=JobStatus.pending,
            progress=0,
            lane=lane_key,
            job_type=(str(job_type).strip() or None) if job_type is not None else None,
            recipe=(recipe if isinstance(recipe, dict) else None),
            parent_job_id=(str(parent_job_id).strip() or None) if parent_job_id is not None else None,
        )
        _job_artifacts[job_id] = []
        _cancel_events[job_id] = asyncio.Event()
        _flush_to_disk()
    return job_id


async def get_job(job_id: str) -> Optional[JobInfo]:
    async with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        return job.model_copy(update={"output_paths": _normalize_output_paths(_job_artifacts.get(job_id) or job.output_paths)})


def _job_runtime_view_locked(job_id: str, job: JobInfo) -> JobInfo:
    lane_key = (str(getattr(job, "lane", "") or "default").strip().lower() or "default")
    lane_limit = _max_parallel_for_lane(lane_key)
    lane_running = 0
    pending_order: list[str] = []
    for jid, row in _jobs.items():
        row_lane = (str(getattr(row, "lane", "") or "default").strip().lower() or "default")
        if row_lane != lane_key:
            continue
        if row.status == JobStatus.running:
            lane_running += 1
        elif row.status == JobStatus.pending:
            pending_order.append(jid)
    queue_position = None
    if job.status == JobStatus.pending:
        try:
            queue_position = pending_order.index(job_id) + 1
        except ValueError:
            queue_position = None
    return job.model_copy(
        update={
            "lane": lane_key,
            "lane_limit": int(lane_limit),
            "lane_running": int(lane_running),
            "lane_pending": int(len(pending_order)),
            "queue_position": queue_position,
            "output_paths": _normalize_output_paths(_job_artifacts.get(job_id) or job.output_paths),
        }
    )


async def get_job_view(job_id: str) -> Optional[JobInfo]:
    """Return job with dynamic queue/lane runtime metrics (not persisted)."""
    async with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        return _job_runtime_view_locked(job_id, job)


async def update_job(
    job_id: str,
    *,
    status: Optional[JobStatus] = None,
    progress: Optional[int] = None,
    progress_message: Optional[str] = None,
    lane: Optional[str] = None,
    output_paths: Optional[list[str]] = None,
    error: Optional[str] = None,
    cancel_requested: Optional[bool] = None,
) -> None:
    async with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        if status is not None:
            job.status = status
        if progress is not None:
            job.progress = progress
        if progress_message is not None:
            job.progress_message = progress_message
        if lane is not None:
            job.lane = lane
        if output_paths is not None:
            norm = _normalize_output_paths(output_paths)
            job.output_paths = norm
            _job_artifacts[job_id] = norm
        if error is not None:
            job.error = error
        if cancel_requested is not None:
            job.cancel_requested = bool(cancel_requested)
        _flush_to_disk()


async def get_lane_stats() -> dict[str, dict[str, int]]:
    """Return runtime lane queue/running counts and configured limits."""
    async with _lock:
        lanes: dict[str, dict[str, int]] = {}
        known_lanes = {"default", "ingest", "audio", "batch"}
        known_lanes.update(
            (str(getattr(j, "lane", "") or "default").strip().lower() or "default")
            for j in _jobs.values()
        )
        for lane_key in sorted(known_lanes):
            pending = 0
            running = 0
            for row in _jobs.values():
                row_lane = (str(getattr(row, "lane", "") or "default").strip().lower() or "default")
                if row_lane != lane_key:
                    continue
                if row.status == JobStatus.pending:
                    pending += 1
                elif row.status == JobStatus.running:
                    running += 1
            lanes[lane_key] = {
                "limit": int(_max_parallel_for_lane(lane_key)),
                "running": int(running),
                "pending": int(pending),
            }
        return lanes


async def request_cancel(job_id: str) -> Optional[JobInfo]:
    """Request cooperative cancellation for a pending/running job."""
    async with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        if job.status in {JobStatus.done, JobStatus.error, JobStatus.cancelled, JobStatus.retrying}:
            return job
        job.cancel_requested = True
        ev = _cancel_events.get(job_id)
        if ev is None:
            ev = asyncio.Event()
            _cancel_events[job_id] = ev
        ev.set()
        task = _job_tasks.get(job_id)
        # If the wrapper task is still waiting in queue/semaphore, cancel it immediately.
        if task is not None and not task.done() and job.status == JobStatus.pending:
            task.cancel()
        _flush_to_disk()
        return job


async def is_cancel_requested(job_id: str) -> bool:
    async with _lock:
        job = _jobs.get(job_id)
        return bool(job and job.cancel_requested)


async def raise_if_cancel_requested(job_id: str) -> None:
    if await is_cancel_requested(job_id):
        raise JobCancelledError(job_id)


async def run_job(
    job_id: str,
    coro: Coroutine,
    *,
    lane: str = "default",
) -> None:
    """Wrap a coroutine as a background job, tracking progress & errors."""
    lane_key = (lane or "default").strip().lower() or "default"
    current_task = asyncio.current_task()
    async with _lock:
        if current_task is not None:
            _job_tasks[job_id] = current_task
        _cancel_events.setdefault(job_id, asyncio.Event())
    await update_job(job_id, status=JobStatus.pending, progress=1, lane=lane_key)
    if await is_cancel_requested(job_id):
        await update_job(job_id, status=JobStatus.cancelled, error="Задача отменена", progress=0)
        async with _lock:
            _job_tasks.pop(job_id, None)
        return
    sem = _semaphore_for_lane(lane_key)
    logger.info("Job %s waiting in lane '%s'", job_id, lane_key)
    try:
        await sem.acquire()
    except asyncio.CancelledError:
        await update_job(job_id, status=JobStatus.cancelled, error="Задача отменена", progress=0)
        async with _lock:
            _job_tasks.pop(job_id, None)
        return
    if await is_cancel_requested(job_id):
        sem.release()
        await update_job(job_id, status=JobStatus.cancelled, error="Задача отменена", progress=0)
        async with _lock:
            _job_tasks.pop(job_id, None)
        return
    await update_job(job_id, status=JobStatus.running, progress=2, lane=lane_key)
    try:
        result = await coro
        # result should be a list of output paths
        paths = result if isinstance(result, list) else [str(result)]
        await update_job(
            job_id,
            status=JobStatus.done,
            progress=100,
            output_paths=[str(p) for p in paths],
        )
    except JobCancelledError:
        await update_job(
            job_id,
            status=JobStatus.cancelled,
            error="Задача отменена",
        )
    except asyncio.CancelledError:
        await update_job(
            job_id,
            status=JobStatus.cancelled,
            error="Задача отменена",
        )
    except Exception as exc:
        logger.exception("Job %s failed", job_id)
        await update_job(
            job_id,
            status=JobStatus.error,
            error=str(exc),
        )
    finally:
        sem.release()
        async with _lock:
            _job_tasks.pop(job_id, None)


async def clear_all_jobs() -> None:
    """Drop all jobs from memory and disk."""
    async with _lock:
        _jobs.clear()
        _job_artifacts.clear()
        _cancel_events.clear()
        _job_tasks.clear()
        _flush_to_disk()


async def remove_document_artifacts(document_id: str) -> int:
    """Remove stored job artifact paths linked to one document."""
    did = str(document_id or "").strip()
    if not did:
        return 0
    changed = 0
    marker = f"{did}"
    async with _lock:
        for job_id, paths in list(_job_artifacts.items()):
            norm = _normalize_output_paths(paths)
            kept = []
            removed_any = False
            for raw in norm:
                value = str(raw or "").strip()
                if not value:
                    continue
                name = Path(value).name
                if name.startswith(marker):
                    removed_any = True
                    continue
                kept.append(value)
            if removed_any:
                _job_artifacts[job_id] = kept
                job = _jobs.get(job_id)
                if job is not None:
                    job.output_paths = kept
                changed += 1
        if changed:
            _flush_to_disk()
    return changed

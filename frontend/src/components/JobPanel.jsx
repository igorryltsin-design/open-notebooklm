import React from "react";
import { useJobPoller } from "../hooks/useJobPoller";
import { cancelJob, downloadUrl, getDocumentArtifacts, retryJob } from "../api/client";
import { useEffect, useMemo, useState } from "react";
import "./JobPanel.css";

export default function JobPanel({ jobId, label, documentId = null, artifactKind = "generic" }) {
  const { job, startPolling } = useJobPoller();
  const [activeJobId, setActiveJobId] = useState(jobId || null);
  const [artifactBusy, setArtifactBusy] = useState(false);
  const [artifactFiles, setArtifactFiles] = useState([]);
  const [artifactChecked, setArtifactChecked] = useState(false);
  const [jobActionBusy, setJobActionBusy] = useState(false);
  const [jobActionError, setJobActionError] = useState("");

  useEffect(() => {
    setActiveJobId(jobId || null);
  }, [jobId]);

  useEffect(() => {
    if (activeJobId) startPolling(activeJobId);
  }, [activeJobId, startPolling]);

  const expectedPattern = useMemo(() => {
    if (!documentId) return null;
    if (artifactKind === "audio") return `${documentId}_podcast.`;
    return `${documentId}_`;
  }, [artifactKind, documentId]);

  async function checkArtifacts() {
    if (!documentId) return;
    setArtifactBusy(true);
    try {
      const res = await getDocumentArtifacts(documentId);
      const files = Array.isArray(res?.files) ? res.files : [];
      const filtered = expectedPattern
        ? files.filter((f) => f.startsWith(expectedPattern))
        : files;
      setArtifactFiles(filtered);
      setArtifactChecked(true);
    } finally {
      setArtifactBusy(false);
    }
  }

  useEffect(() => {
    setArtifactFiles([]);
    setArtifactChecked(false);
    if (!documentId) return;
    checkArtifacts();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [documentId, artifactKind]);

  const hasFallbackArtifacts = artifactFiles.length > 0;
  const status = job?.status || (hasFallbackArtifacts ? "done" : "pending");
  const statusLabel = {
    pending: "ожидание",
    running: "выполняется",
    retrying: "перезапуск",
    done: "готово",
    error: "ошибка",
    cancelled: "отменено",
  }[status] || status;
  const outputPaths = status === "done"
    ? ((job?.output_paths && job.output_paths.length > 0)
      ? job.output_paths
      : artifactFiles.map((f) => `/data/outputs/${f}`))
    : (job?.output_paths || []);
  const progress = job?.progress ?? (hasFallbackArtifacts ? 100 : 0);
  const jobType = String(job?.job_type || "").toLowerCase();
  const canRetry = !!activeJobId && (jobType === "audio" || jobType === "batch") && ["done", "error", "cancelled"].includes(status);
  const canCancel = !!activeJobId && ["pending", "running"].includes(status);
  const laneName = String(job?.lane || "").trim();
  const laneLimit = Number.isFinite(Number(job?.lane_limit)) ? Number(job.lane_limit) : null;
  const laneRunning = Number.isFinite(Number(job?.lane_running)) ? Number(job.lane_running) : null;
  const lanePending = Number.isFinite(Number(job?.lane_pending)) ? Number(job.lane_pending) : null;
  const queuePosition = Number.isFinite(Number(job?.queue_position)) ? Number(job.queue_position) : null;
  const hasLaneMeta = !!laneName && (laneLimit !== null || laneRunning !== null || lanePending !== null || queuePosition !== null);
  const shortJobId = String(activeJobId || "").trim();

  if (!job && artifactChecked && !hasFallbackArtifacts) return null;

  async function handleCancel() {
    if (!activeJobId || jobActionBusy) return;
    setJobActionBusy(true);
    setJobActionError("");
    try {
      await cancelJob(activeJobId);
      startPolling(activeJobId);
    } catch (e) {
      setJobActionError(e?.message || "Не удалось отменить задачу");
    } finally {
      setJobActionBusy(false);
    }
  }

  async function handleRetry() {
    if (!activeJobId || jobActionBusy) return;
    setJobActionBusy(true);
    setJobActionError("");
    try {
      const res = await retryJob(activeJobId);
      const nextId = res?.job_id ? String(res.job_id) : "";
      if (!nextId) throw new Error("Backend не вернул новый job_id");
      setActiveJobId(nextId);
      startPolling(nextId);
    } catch (e) {
      setJobActionError(e?.message || "Не удалось перезапустить задачу");
    } finally {
      setJobActionBusy(false);
    }
  }

  return (
    <div className="card job-panel">
      <div className="job-header">
        <div className="job-header-main">
          <h3>{label}</h3>
          <div className="job-header-sub">
            {shortJobId ? <span className="job-id-chip">#{shortJobId.slice(0, 8)}</span> : null}
            <span className={`badge ${status}`}>{statusLabel}</span>
          </div>
        </div>
      </div>

      <div className="progress-bar">
        <div className="fill" style={{ width: `${progress}%` }} />
      </div>
      <span className="progress-label">{progress}%</span>
      {hasLaneMeta && (
        <div className="job-runtime-meta" aria-label="Метаданные очереди">
          <span className="job-meta-chip">
            Очередь {laneName}
            {laneRunning !== null && laneLimit !== null ? ` · выполняется ${laneRunning}/${laneLimit}` : ""}
            {lanePending !== null ? ` · ожидает ${lanePending}` : ""}
          </span>
          {status === "pending" && queuePosition !== null && (
            <span className="job-meta-chip">Позиция в очереди: #{queuePosition}</span>
          )}
        </div>
      )}

      {job?.error && <p className="job-error">{job.error}</p>}
      {jobActionError && <p className="job-error">{jobActionError}</p>}

      <div className="job-toolbar">
        {(canCancel || canRetry) && (
          <div className="job-artifact-actions">
            {canCancel && (
              <button type="button" className="secondary small" onClick={handleCancel} disabled={jobActionBusy}>
                {jobActionBusy ? "Отмена…" : "Отменить"}
              </button>
            )}
            {canRetry && (
              <button type="button" className="secondary small" onClick={handleRetry} disabled={jobActionBusy}>
                {jobActionBusy ? "Запуск…" : "Повторить"}
              </button>
            )}
          </div>
        )}
        {documentId && (
          <button type="button" className="secondary small" onClick={checkArtifacts} disabled={artifactBusy}>
            {artifactBusy ? "Проверка…" : "Проверить артефакты"}
          </button>
        )}
      </div>

      {status === "done" && outputPaths && outputPaths.length > 0 && (
        <details className="job-downloads" open>
          <summary>Артефакты ({outputPaths.length})</summary>
          <div className="download-links">
            {outputPaths.map((p, i) => {
              const filename = p.split("/").pop();
              return (
                <a
                  key={i}
                  href={downloadUrl(filename)}
                  className="download-btn"
                  download
                >
                  Скачать {filename}
                </a>
              );
            })}
          </div>
        </details>
      )}
    </div>
  );
}

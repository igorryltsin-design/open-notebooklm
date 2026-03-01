import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  importScript,
  getScriptTtsQuality,
  getScriptLocks,
  saveScriptLocks,
  previewScriptLine,
  regenerateScriptLine,
  downloadUrl,
  getScriptTimeline,
  downloadScriptExport,
  getScriptMetrics,
  getScriptVersions,
  compareScriptVersions,
  restoreScriptVersion,
  downloadDocumentBundle,
  downloadReportDocx,
} from "../api/client";
import "./ScriptPanel.css";

const VOICE_COLORS = {
  host: "#6c5ce7",
  guest1: "#00cec9",
  guest2: "#fdcb6e",
};

const VOICE_COLOR_PALETTE = [
  "#e17055",
  "#0984e3",
  "#00b894",
  "#fdcb6e",
  "#6c5ce7",
  "#d63031",
  "#00cec9",
  "#e84393",
  "#2d98da",
  "#20bf6b",
];

function buildVoiceColorMap(lines) {
  const map = { ...VOICE_COLORS };
  const used = new Set(Object.values(map));
  let paletteIdx = 0;
  for (const line of Array.isArray(lines) ? lines : []) {
    const voice = String(line?.voice || "").trim();
    if (!voice || map[voice]) continue;
    while (paletteIdx < VOICE_COLOR_PALETTE.length && used.has(VOICE_COLOR_PALETTE[paletteIdx])) {
      paletteIdx += 1;
    }
    const color = VOICE_COLOR_PALETTE[paletteIdx] || "#8888aa";
    map[voice] = color;
    used.add(color);
    paletteIdx += 1;
  }
  return map;
}

function splitReasoning(rawText) {
  const src = String(rawText || "");
  const re = /<think>([\s\S]*?)<\/think>/gi;
  const chunks = [];
  let answer = "";
  let last = 0;
  let m;
  while ((m = re.exec(src)) !== null) {
    answer += src.slice(last, m.index);
    chunks.push(String(m[1] || "").trim());
    last = re.lastIndex;
  }
  answer += src.slice(last);

  const openIdx = answer.toLowerCase().lastIndexOf("<think>");
  if (openIdx >= 0) {
    chunks.push(answer.slice(openIdx + "<think>".length).trim());
    answer = answer.slice(0, openIdx);
  }

  answer = answer.replace(/<\/think>/gi, "").trim();
  const reasoning = chunks.filter(Boolean).join("\n\n").trim();
  return { answer, reasoning };
}

function normalizeSpaces(text) {
  return String(text || "").replace(/\s+/g, " ").trim();
}

function splitLongLineForTts(text) {
  const src = normalizeSpaces(text);
  if (!src) return [];
  if (src.length <= 260) return [src];

  const sentences = src.split(/(?<=[.!?…])\s+/).map((s) => s.trim()).filter(Boolean);
  if (sentences.length >= 2) {
    const totalLen = sentences.join(" ").length;
    let acc = "";
    const left = [];
    const right = [...sentences];
    while (right.length > 1 && acc.length < totalLen / 2) {
      const next = right.shift();
      left.push(next);
      acc = left.join(" ");
    }
    if (left.length && right.length) {
      return [left.join(" ").trim(), right.join(" ").trim()].filter(Boolean);
    }
  }

  const commaChunks = src.split(/,\s+/).map((s) => s.trim()).filter(Boolean);
  if (commaChunks.length >= 2) {
    const totalLen = commaChunks.join(", ").length;
    let acc = "";
    const left = [];
    const right = [...commaChunks];
    while (right.length > 1 && acc.length < totalLen / 2) {
      const next = right.shift();
      left.push(next);
      acc = left.join(", ");
    }
    if (left.length && right.length) {
      return [
        `${left.join(", ").trim()},`.replace(/\s+,/g, ","),
        right.join(", ").trim(),
      ].filter(Boolean);
    }
  }

  const words = src.split(" ").filter(Boolean);
  if (words.length >= 8) {
    const mid = Math.floor(words.length / 2);
    return [words.slice(0, mid).join(" "), words.slice(mid).join(" ")].filter(Boolean);
  }
  return [src];
}

function shiftLockMapForInsertedLine(prev, index, insertedCount) {
  if (!prev || !insertedCount) return prev || {};
  const shift = Math.max(0, Number(insertedCount) || 0);
  const cut = Math.max(0, Number(index) || 0);
  const next = {};
  for (const [k, v] of Object.entries(prev)) {
    if (!v) continue;
    const idx = Number(k);
    if (!Number.isInteger(idx)) continue;
    next[idx > cut ? idx + shift : idx] = true;
  }
  return next;
}

function lockStorageKey(documentId) {
  return `script-line-locks:${documentId || "none"}`;
}

function regenInstructionStorageKey(documentId) {
  return `script-line-regen-instruction:${documentId || "none"}`;
}

function scriptFilterStorageKey(documentId) {
  return `script-panel-filters:${documentId || "none"}`;
}

function ScriptActionIcon({ name }) {
  if (name === "play") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M8 6v12l10-6z" />
      </svg>
    );
  }
  if (name === "stop") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M7 7h10v10H7z" />
      </svg>
    );
  }
  if (name === "regen") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M12 5a7 7 0 1 1-6.6 9h2.2A5 5 0 1 0 9 8.4L11.2 10H5V3.8l2.3 2.3A7 7 0 0 1 12 5Z" />
      </svg>
    );
  }
  if (name === "lock") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M7 10V8a5 5 0 1 1 10 0v2h1a2 2 0 0 1 2 2v8H4v-8a2 2 0 0 1 2-2h1Zm2 0h6V8a3 3 0 1 0-6 0v2Z" />
      </svg>
    );
  }
  if (name === "unlock") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M17 10h1a2 2 0 0 1 2 2v8H4v-8a2 2 0 0 1 2-2h9V8a3 3 0 0 0-6 0h2a1 1 0 1 1 0 2H7V8a5 5 0 0 1 10 0v2Z" />
      </svg>
    );
  }
  if (name === "tools") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="m19.4 13 .2-1-.2-1 2-1.6-2-3.4-2.4.7a7 7 0 0 0-1.7-1L14.9 3h-3.8l-.4 2.7a7 7 0 0 0-1.7 1l-2.4-.7-2 3.4 2 1.6-.2 1 .2 1-2 1.6 2 3.4 2.4-.7a7 7 0 0 0 1.7 1l.4 2.7h3.8l.4-2.7a7 7 0 0 0 1.7-1l2.4.7 2-3.4-2-1.6ZM12 15.2A3.2 3.2 0 1 1 12 8.8a3.2 3.2 0 0 1 0 6.4Z" />
      </svg>
    );
  }
  if (name === "chevron") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="m7 10 5 5 5-5z" />
      </svg>
    );
  }
  return null;
}

export default function ScriptPanel({ script, documentId, streamingRaw, onScriptImported, onError }) {
  const fileInputRef = useRef(null);
  const audioRef = useRef(null);
  const currentPreviewIndexRef = useRef(null);
  const lockSyncReadyRef = useRef(false);
  const lockSaveTimerRef = useRef(null);
  const [isEditing, setIsEditing] = useState(false);
  const [draft, setDraft] = useState(script);
  const [ttsQuality, setTtsQuality] = useState(null);
  const [loadingQuality, setLoadingQuality] = useState(false);
  const [previewStatus, setPreviewStatus] = useState({});
  const [timeline, setTimeline] = useState(null);
  const [loadingTimeline, setLoadingTimeline] = useState(false);
  const [metrics, setMetrics] = useState(null);
  const [loadingMetrics, setLoadingMetrics] = useState(false);
  const [editMode, setEditMode] = useState("lines"); // "lines" | "blocks"
  const [blockDraft, setBlockDraft] = useState("");
  const [lockedLines, setLockedLines] = useState({});
  const [regenStatus, setRegenStatus] = useState({});
  const [regenInstruction, setRegenInstruction] = useState("");
  const [normStatus, setNormStatus] = useState({});
  const [versions, setVersions] = useState([]);
  const [currentVersionId, setCurrentVersionId] = useState("");
  const [leftVersionId, setLeftVersionId] = useState("");
  const [rightVersionId, setRightVersionId] = useState("");
  const [versionDiff, setVersionDiff] = useState(null);
  const [loadingVersions, setLoadingVersions] = useState(false);
  const [compareLoading, setCompareLoading] = useState(false);
  const [restoreLoadingId, setRestoreLoadingId] = useState("");
  const [lineQuery, setLineQuery] = useState("");
  const [voiceFilter, setVoiceFilter] = useState("all");
  const [issuesOnly, setIssuesOnly] = useState(false);
  const hasPlayingPreview = Object.values(previewStatus).includes("playing");
  const hasStreamingRaw = !!streamingRaw;
  const hasScriptLines = Array.isArray(script) && script.length > 0;

  function stopCurrentPreview() {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.currentTime = 0;
      audioRef.current = null;
    }
    const prevIndex = currentPreviewIndexRef.current;
    currentPreviewIndexRef.current = null;
    if (typeof prevIndex === "number") {
      setPreviewStatus((prev) => ({ ...prev, [prevIndex]: null }));
    }
  }

  useEffect(() => () => stopCurrentPreview(), []);

  useEffect(() => () => {
    if (lockSaveTimerRef.current) {
      clearTimeout(lockSaveTimerRef.current);
      lockSaveTimerRef.current = null;
    }
  }, []);

  useEffect(() => {
    setDraft(script);
    setLockedLines((prev) => {
      const next = {};
      const max = Array.isArray(script) ? script.length : 0;
      Object.entries(prev || {}).forEach(([k, v]) => {
        const idx = Number(k);
        if (Number.isInteger(idx) && idx >= 0 && idx < max && v) next[idx] = true;
      });
      return next;
    });
    setRegenStatus({});
    setNormStatus({});
    stopCurrentPreview();
  }, [script]);

  useEffect(() => {
    if (!documentId) {
      setLineQuery("");
      setVoiceFilter("all");
      setIssuesOnly(false);
      return;
    }
    try {
      const raw = sessionStorage.getItem(scriptFilterStorageKey(documentId));
      if (!raw) {
        setLineQuery("");
        setVoiceFilter("all");
        setIssuesOnly(false);
        return;
      }
      const parsed = JSON.parse(raw);
      setLineQuery(String(parsed?.query || ""));
      setVoiceFilter(String(parsed?.voice || "all") || "all");
      setIssuesOnly(!!parsed?.issuesOnly);
    } catch (_) {
      setLineQuery("");
      setVoiceFilter("all");
      setIssuesOnly(false);
    }
  }, [documentId]);

  useEffect(() => {
    if (!documentId) {
      lockSyncReadyRef.current = false;
      setLockedLines({});
      setRegenInstruction("");
      setRegenStatus({});
      setNormStatus({});
      return;
    }
    try {
      const rawLocks = localStorage.getItem(lockStorageKey(documentId));
      if (rawLocks) {
        const parsed = JSON.parse(rawLocks);
        if (parsed && typeof parsed === "object") {
          const next = {};
          Object.entries(parsed).forEach(([k, v]) => {
            const idx = Number(k);
            if (Number.isInteger(idx) && idx >= 0 && !!v) next[idx] = true;
          });
          setLockedLines(next);
        } else {
          setLockedLines({});
        }
      } else {
        setLockedLines({});
      }
    } catch (_) {
      setLockedLines({});
    }
    try {
      setRegenInstruction(localStorage.getItem(regenInstructionStorageKey(documentId)) || "");
    } catch (_) {
      setRegenInstruction("");
    }
    lockSyncReadyRef.current = false;
    let cancelled = false;
    getScriptLocks(documentId)
      .then((res) => {
        if (cancelled) return;
        const rows = Array.isArray(res?.locks) ? res.locks : [];
        const next = {};
        rows.forEach((idx) => {
          const n = Number(idx);
          if (Number.isInteger(n) && n >= 0) next[n] = true;
        });
        setLockedLines(next);
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) lockSyncReadyRef.current = true;
      });
    setRegenStatus({});
    setNormStatus({});
    return () => {
      cancelled = true;
      lockSyncReadyRef.current = false;
    };
  }, [documentId]);

  useEffect(() => {
    if (!documentId) return;
    try {
      localStorage.setItem(lockStorageKey(documentId), JSON.stringify(lockedLines || {}));
    } catch (_) {}
  }, [documentId, lockedLines]);

  useEffect(() => {
    if (!documentId || !lockSyncReadyRef.current) return;
    if (lockSaveTimerRef.current) {
      clearTimeout(lockSaveTimerRef.current);
      lockSaveTimerRef.current = null;
    }
    const locks = Object.entries(lockedLines || {})
      .filter(([, v]) => !!v)
      .map(([k]) => Number(k))
      .filter((n) => Number.isInteger(n) && n >= 0)
      .sort((a, b) => a - b);
    lockSaveTimerRef.current = setTimeout(() => {
      lockSaveTimerRef.current = null;
      saveScriptLocks(documentId, locks).catch(() => {});
    }, 250);
    return () => {
      if (lockSaveTimerRef.current) {
        clearTimeout(lockSaveTimerRef.current);
        lockSaveTimerRef.current = null;
      }
    };
  }, [documentId, lockedLines]);

  useEffect(() => {
    if (!documentId) return;
    try {
      localStorage.setItem(regenInstructionStorageKey(documentId), String(regenInstruction || ""));
    } catch (_) {}
  }, [documentId, regenInstruction]);

  useEffect(() => {
    if (!documentId || !script || script.length === 0) {
      setTtsQuality(null);
      return;
    }
    let cancelled = false;
    setLoadingQuality(true);
    getScriptTtsQuality(documentId)
      .then((res) => {
        if (!cancelled) setTtsQuality(res);
      })
      .catch(() => {
        if (!cancelled) setTtsQuality(null);
      })
      .finally(() => {
        if (!cancelled) setLoadingQuality(false);
      });
    return () => {
      cancelled = true;
    };
  }, [documentId, script]);

  useEffect(() => {
    if (!documentId || !script || script.length === 0) {
      setMetrics(null);
      return;
    }
    let cancelled = false;
    setLoadingMetrics(true);
    getScriptMetrics(documentId)
      .then((res) => {
        if (!cancelled) setMetrics(res);
      })
      .catch(() => {
        if (!cancelled) setMetrics(null);
      })
      .finally(() => {
        if (!cancelled) setLoadingMetrics(false);
      });
    return () => {
      cancelled = true;
    };
  }, [documentId, script]);

  useEffect(() => {
    if (!documentId || !script || script.length === 0) {
      setTimeline(null);
      return;
    }
    let cancelled = false;
    setLoadingTimeline(true);
    getScriptTimeline(documentId)
      .then((res) => {
        if (!cancelled) setTimeline(res);
      })
      .catch(() => {
        if (!cancelled) setTimeline(null);
      })
      .finally(() => {
        if (!cancelled) setLoadingTimeline(false);
      });
    return () => {
      cancelled = true;
    };
  }, [documentId, script]);

  useEffect(() => {
    if (!documentId || !script || script.length === 0) {
      setVersions([]);
      setCurrentVersionId("");
      setLeftVersionId("");
      setRightVersionId("");
      setVersionDiff(null);
      return;
    }
    let cancelled = false;
    setLoadingVersions(true);
    getScriptVersions(documentId)
      .then((res) => {
        if (cancelled) return;
        const rows = Array.isArray(res?.versions) ? res.versions : [];
        const current = String(res?.current_version_id || "");
        setVersions(rows);
        setCurrentVersionId(current);
        const fallbackRight = current || String(rows[rows.length - 1]?.version_id || "");
        const fallbackLeft = rows.length >= 2 ? String(rows[rows.length - 2]?.version_id || "") : fallbackRight;
        setRightVersionId((prev) => {
          const valid = rows.some((x) => String(x?.version_id || "") === String(prev || ""));
          return valid ? prev : fallbackRight;
        });
        setLeftVersionId((prev) => {
          const valid = rows.some((x) => String(x?.version_id || "") === String(prev || ""));
          return valid ? prev : fallbackLeft;
        });
      })
      .catch(() => {
        if (!cancelled) {
          setVersions([]);
          setCurrentVersionId("");
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingVersions(false);
      });
    return () => {
      cancelled = true;
    };
  }, [documentId, script]);

  function handleExport() {
    const blob = new Blob([JSON.stringify({ script }, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `скрипт-подкаста-${documentId || "экспорт"}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  async function handleExportFormat(format) {
    try {
      await downloadScriptExport(documentId, format);
    } catch (err) {
      onError?.(err.message || "Не удалось экспортировать файл");
    }
  }

  async function handleExportBundle() {
    try {
      await downloadDocumentBundle(documentId);
    } catch (err) {
      onError?.(err.message || "Не удалось экспортировать bundle");
    }
  }

  async function handleExportReportDocx() {
    try {
      await downloadReportDocx(documentId);
    } catch (err) {
      onError?.(err.message || "Не удалось экспортировать DOCX-отчет");
    }
  }

  async function handleImport(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const text = await file.text();
      const data = JSON.parse(text);
      const list = data.script || data;
      if (!Array.isArray(list) || list.length === 0) {
        onError?.("В JSON нужен массив script: [{ voice, text }, ...]");
        return;
      }
      const res = await importScript(documentId, list);
      onScriptImported?.(res.script);
    } catch (err) {
      onError?.(err.message || "Ошибка чтения JSON");
    }
    e.target.value = "";
  }

  function handleEditToggle() {
    setDraft(script);
    setBlockDraft(scriptToBlocks(script || []));
    setEditMode("lines");
    setIsEditing((v) => !v);
  }

  function handleDraftChange(index, newText) {
    if (lockedLines?.[index]) return;
    setDraft((prev) =>
      prev.map((line, i) =>
        i === index
          ? {
              ...line,
              text: newText,
            }
          : line,
      ),
    );
  }

  async function handleSaveEdits() {
    try {
      let list = Array.isArray(draft) ? draft : script;
      if (editMode === "blocks") {
        list = blocksToScript(blockDraft);
      }
      if (!Array.isArray(list) || list.length === 0) {
        onError?.("После редактирования должен остаться хотя бы один блок с текстом.");
        return;
      }
      const res = await importScript(documentId, list);
      onScriptImported?.(res.script);
      setIsEditing(false);
    } catch (err) {
      onError?.(err.message || "Не удалось сохранить изменённый скрипт");
    }
  }

  function handleCancelEdits() {
    setDraft(script);
    setBlockDraft(scriptToBlocks(script || []));
    setEditMode("lines");
    setIsEditing(false);
  }

  function scriptToBlocks(list) {
    return (list || [])
      .map((line) => `${line.voice}\n${line.text || ""}`.trim())
      .join("\n\n---\n\n");
  }

  function blocksToScript(text) {
    const rawBlocks = (text || "")
      .split(/\n\s*---\s*\n/g)
      .map((b) => b.trim())
      .filter(Boolean);
    const parsed = [];
    for (const block of rawBlocks) {
      const lines = block.split("\n");
      const voice = (lines.shift() || "Игорь").trim() || "Игорь";
      const body = lines.join("\n").trim();
      if (!body) continue;
      parsed.push({ voice, text: body });
    }
    return parsed;
  }

  async function handlePreviewLine(index, line) {
    if (currentPreviewIndexRef.current === index && audioRef.current) {
      stopCurrentPreview();
      return;
    }

    setPreviewStatus((prev) => ({ ...prev, [index]: "loading" }));
    try {
      const res = await previewScriptLine(documentId, { voice: line.voice, text: line.text });
      stopCurrentPreview();
      const audio = new Audio(downloadUrl(res.filename));
      audioRef.current = audio;
      currentPreviewIndexRef.current = index;
      setPreviewStatus((prev) => ({ ...prev, [index]: "playing" }));
      audio.onended = () => {
        if (currentPreviewIndexRef.current === index) {
          currentPreviewIndexRef.current = null;
          audioRef.current = null;
          setPreviewStatus((prev) => ({ ...prev, [index]: null }));
        }
      };
      await audio.play();
    } catch (err) {
      if (currentPreviewIndexRef.current === index) {
        currentPreviewIndexRef.current = null;
      }
      if (audioRef.current) {
        audioRef.current = null;
      }
      setPreviewStatus((prev) => ({ ...prev, [index]: "error" }));
      onError?.(err.message || "Не удалось предпрослушать реплику");
    }
  }

  function toggleLineLock(index) {
    setLockedLines((prev) => ({ ...prev, [index]: !prev?.[index] }));
  }

  async function handleRegenerateLine(index) {
    if (lockedLines?.[index]) return;
    setRegenStatus((prev) => ({ ...prev, [index]: "loading" }));
    try {
      const res = await regenerateScriptLine(documentId, {
        line_index: index,
        instruction: String(regenInstruction || "").trim(),
        tts_friendly: true,
      });
      if (!res || !Array.isArray(res.script)) {
        throw new Error("Backend не вернул обновлённый скрипт");
      }
      setRegenStatus((prev) => ({ ...prev, [index]: "done" }));
      onScriptImported?.(res.script);
    } catch (err) {
      setRegenStatus((prev) => ({ ...prev, [index]: "error" }));
      onError?.(err.message || "Не удалось перегенерировать реплику");
    }
  }

  async function handleApplyNormalization(index, line, qualityLine) {
    if (lockedLines?.[index]) return;
    const issues = Array.isArray(qualityLine?.issues) ? qualityLine.issues : [];
    const hasLongLine = issues.some((it) => String(it?.code || "") === "long_line");
    const suggested = normalizeSpaces(qualityLine?.suggestion || line?.text || "");
    if (!suggested) return;

    const parts = hasLongLine ? splitLongLineForTts(suggested) : [suggested];
    const finalParts = (parts.length ? parts : [suggested]).map(normalizeSpaces).filter(Boolean);
    if (!finalParts.length) return;

    const base = Array.isArray(script) ? script.map((x) => ({ ...x })) : [];
    if (!base[index]) return;
    const replacement = finalParts.map((text) => ({ voice: base[index].voice, text }));
    const nextScript = [...base.slice(0, index), ...replacement, ...base.slice(index + 1)];

    setNormStatus((prev) => ({ ...prev, [index]: "loading" }));
    try {
      if (replacement.length > 1) {
        setLockedLines((prev) => shiftLockMapForInsertedLine(prev, index, replacement.length - 1));
      }
      const res = await importScript(documentId, nextScript);
      if (!res || !Array.isArray(res.script)) {
        throw new Error("Backend не вернул обновлённый скрипт");
      }
      setNormStatus((prev) => ({ ...prev, [index]: "done" }));
      onScriptImported?.(res.script);
    } catch (err) {
      setNormStatus((prev) => ({ ...prev, [index]: "error" }));
      onError?.(err.message || "Не удалось применить нормализацию");
    }
  }

  async function handleCompareVersions() {
    if (!documentId) return;
    if (!leftVersionId || !rightVersionId) {
      onError?.("Выберите обе версии для сравнения");
      return;
    }
    setCompareLoading(true);
    try {
      const res = await compareScriptVersions(documentId, {
        left_version_id: leftVersionId,
        right_version_id: rightVersionId,
      });
      setVersionDiff(res || null);
    } catch (err) {
      onError?.(err.message || "Не удалось сравнить версии скрипта");
    } finally {
      setCompareLoading(false);
    }
  }

  async function handleRestoreVersion(versionId) {
    const target = String(versionId || "").trim();
    if (!target || !documentId) return;
    setRestoreLoadingId(target);
    try {
      const res = await restoreScriptVersion(documentId, target);
      if (!Array.isArray(res?.script)) {
        throw new Error("Backend не вернул скрипт после восстановления версии");
      }
      setVersionDiff(null);
      onScriptImported?.(res.script);
    } catch (err) {
      onError?.(err.message || "Не удалось восстановить версию скрипта");
    } finally {
      setRestoreLoadingId("");
    }
  }

  const qualityByIndex = useMemo(() => {
    const map = new Map();
    const lines = Array.isArray(ttsQuality?.lines) ? ttsQuality.lines : [];
    lines.forEach((row) => {
      const idx = Number(row?.index);
      if (Number.isInteger(idx) && idx >= 0) map.set(idx, row);
    });
    return map;
  }, [ttsQuality]);

  const visibleLines = Array.isArray(draft) && draft.length ? draft : Array.isArray(script) ? script : [];
  const voiceOptions = useMemo(() => {
    const set = new Set();
    visibleLines.forEach((line) => {
      const voice = String(line?.voice || "").trim();
      if (voice) set.add(voice);
    });
    return Array.from(set.values());
  }, [visibleLines]);

  const filteredRows = useMemo(() => {
    const query = String(lineQuery || "").trim().toLowerCase();
    return visibleLines
      .map((line, index) => ({ line, index }))
      .filter(({ line, index }) => {
        if (voiceFilter !== "all" && String(line?.voice || "") !== voiceFilter) return false;
        if (issuesOnly) {
          const q = qualityByIndex.get(index);
          const hasIssues = Array.isArray(q?.issues) && q.issues.length > 0;
          if (!hasIssues) return false;
        }
        if (!query) return true;
        const haystack = `${String(line?.voice || "")} ${String(line?.text || "")}`.toLowerCase();
        return haystack.includes(query);
      });
  }, [visibleLines, voiceFilter, issuesOnly, lineQuery, qualityByIndex]);

  useEffect(() => {
    if (!documentId) return;
    try {
      sessionStorage.setItem(
        scriptFilterStorageKey(documentId),
        JSON.stringify({
          query: String(lineQuery || ""),
          voice: String(voiceFilter || "all") || "all",
          issuesOnly: !!issuesOnly,
        }),
      );
    } catch (_) {}
  }, [documentId, lineQuery, voiceFilter, issuesOnly]);

  useEffect(() => {
    if (voiceFilter === "all") return;
    if (voiceOptions.includes(voiceFilter)) return;
    setVoiceFilter("all");
  }, [voiceFilter, voiceOptions]);

  if (hasStreamingRaw) {
    const { answer, reasoning } = splitReasoning(streamingRaw);
    return (
      <div className="card script-panel">
        <div className="script-panel-header">
          <h3>Скрипт подкаста <span className="streaming-badge">Пишу скрипт…</span></h3>
        </div>
        <pre className="script-streaming-raw">{answer}</pre>
        {reasoning && (
          <details className="script-think">
            <summary>Рассуждение модели</summary>
            <pre className="script-think-body">{reasoning}</pre>
          </details>
        )}
      </div>
    );
  }

  if (!hasScriptLines) {
    return (
      <div className="card script-panel">
        <div className="script-panel-header">
          <div className="script-panel-head-main">
            <h3>Скрипт подкаста</h3>
            <div className="script-panel-head-meta">
              <span className="script-meta-chip">Реплик: 0</span>
            </div>
          </div>
        </div>
        <div className="script-empty is-compact">
          Скрипт ещё не создан. Запустите шаг «3. Сгенерировать скрипт».
        </div>
      </div>
    );
  }

  return (
    <div className="card script-panel">
      <div className="script-panel-header">
        <div className="script-panel-head-main">
          <h3>Скрипт подкаста</h3>
          <div className="script-panel-head-meta">
            <span className="script-meta-chip">Реплик: {Array.isArray(script) ? script.length : 0}</span>
            <span className="script-meta-chip">
              Lock: {Object.values(lockedLines || {}).filter(Boolean).length}
            </span>
            <span className="script-meta-chip">
              Показано: {filteredRows.length}/{visibleLines.length}
            </span>
            <span className="script-meta-chip script-grounding-legend">метка: вне документа</span>
          </div>
        </div>
        <div className="script-toolbar">
          <div className="script-toolbar-main">
            {!isEditing && (
              <button
                type="button"
                className="secondary"
                onClick={handleEditToggle}
                title="Редактировать скрипт вручную"
              >
                Редактировать
              </button>
            )}
            {!isEditing && hasPlayingPreview && (
              <button
                type="button"
                className="secondary"
                onClick={stopCurrentPreview}
                title="Остановить текущее предпрослушивание"
              >
                Стоп прослушивание
              </button>
            )}
            {isEditing && (
              <>
                <div className="edit-mode-switch">
                  <button
                    type="button"
                    className={editMode === "lines" ? "" : "secondary"}
                    onClick={() => setEditMode("lines")}
                    title="Редактировать каждый блок отдельно"
                  >
                    По строкам
                  </button>
                  <button
                    type="button"
                    className={editMode === "blocks" ? "" : "secondary"}
                    onClick={() => setEditMode("blocks")}
                    title="Редактировать весь скрипт одним текстом: блоки разделяются строкой ---"
                  >
                    Единый блок
                  </button>
                </div>
                <button
                  type="button"
                  onClick={handleSaveEdits}
                  title="Сохранить изменения скрипта"
                >
                  Сохранить
                </button>
                <button
                  type="button"
                  className="secondary"
                  onClick={handleCancelEdits}
                  title="Отменить редактирование"
                >
                  Отмена
                </button>
              </>
            )}
            <details className="script-tools-menu">
              <summary>
                <span className="script-tools-icon"><ScriptActionIcon name="tools" /></span>
                Инструменты
                <span className="script-tools-chevron"><ScriptActionIcon name="chevron" /></span>
              </summary>
              <div className="script-tools-grid">
                <button type="button" className="secondary small" onClick={handleExport} title="Скачать скрипт в JSON">
                  Экспорт в JSON
                </button>
                <button type="button" className="secondary small" onClick={() => handleExportFormat("txt")} title="Скачать скрипт в TXT">
                  TXT
                </button>
                <button type="button" className="secondary small" onClick={() => handleExportFormat("srt")} title="Скачать субтитры в SRT">
                  SRT
                </button>
                <button type="button" className="secondary small" onClick={() => handleExportFormat("docx")} title="Скачать скрипт в DOCX">
                  DOCX
                </button>
                <button type="button" className="secondary small" onClick={handleExportReportDocx} title="Скачать расширенный DOCX-отчет (summary + script + метрики)">
                  DOCX+
                </button>
                <button type="button" className="secondary small" onClick={handleExportBundle} title="Скачать единый ZIP-пакет артефактов документа">
                  ZIP bundle
                </button>
                <button type="button" className="secondary small" onClick={() => fileInputRef.current?.click()} title="Загрузить скрипт из JSON-файла">
                  Импорт из JSON
                </button>
              </div>
            </details>
            <input ref={fileInputRef} type="file" accept=".json" hidden onChange={handleImport} />
          </div>
        </div>
      </div>
      {!isEditing && (
        <div className="script-filter-bar">
          <select value={voiceFilter} onChange={(e) => setVoiceFilter(e.target.value)} title="Фильтр по голосу">
            <option value="all">Все голоса</option>
            {voiceOptions.map((voice) => (
              <option key={voice} value={voice}>{voice}</option>
            ))}
          </select>
          <input
            type="text"
            value={lineQuery}
            onChange={(e) => setLineQuery(e.target.value)}
            placeholder="Поиск по репликам"
            title="Фильтр по тексту и имени голоса"
          />
          <label className="script-filter-issues" title="Показывать только реплики с замечаниями TTS-проверки">
            <input type="checkbox" checked={issuesOnly} onChange={(e) => setIssuesOnly(e.target.checked)} />
            Только с замечаниями
          </label>
          <button
            type="button"
            className="secondary small"
            onClick={() => {
              setLineQuery("");
              setVoiceFilter("all");
              setIssuesOnly(false);
            }}
            disabled={!lineQuery && voiceFilter === "all" && !issuesOnly}
            title="Сбросить фильтры списка реплик"
          >
            Сброс фильтров
          </button>
        </div>
      )}
      {!isEditing && (
        <details className="script-regen-panel">
          <summary>Точечная регенерация реплик</summary>
          <div className="script-regen-toolbar">
            <label htmlFor="script-regen-instruction" className="text-muted">
              Инструкция для кнопки «Перегенерировать»
            </label>
            <div className="script-regen-toolbar-row">
              <input
                id="script-regen-instruction"
                type="text"
                value={regenInstruction}
                onChange={(e) => setRegenInstruction(e.target.value)}
                placeholder="Например: короче, более формально, добавить пример, мягче тон"
                title="Эта инструкция будет применяться при нажатии «Перегенерировать» для конкретной реплики"
              />
              <button
                type="button"
                className="secondary"
                onClick={() => setRegenInstruction("")}
                disabled={!String(regenInstruction || "").trim()}
                title="Очистить инструкцию регенерации"
              >
                Очистить
              </button>
            </div>
          </div>
        </details>
      )}
      {isEditing && editMode === "blocks" ? (
        <div className="block-editor-wrap">
          <p className="text-muted">Формат: первая строка блока — имя голоса, далее текст. Разделитель блоков: <code>---</code>.</p>
          <textarea
            className="block-editor"
            value={blockDraft}
            onChange={(e) => setBlockDraft(e.target.value)}
            rows={18}
          />
        </div>
      ) : (
        <div className="script-lines">
          {(() => {
            const voiceColorMap = buildVoiceColorMap(visibleLines);
            if (filteredRows.length === 0) {
              return (
                <div className="script-lines-empty">
                  По текущим фильтрам реплик не найдено. Очистите фильтры и попробуйте снова.
                </div>
              );
            }
            return filteredRows.map(({ line, index: i }) => {
              const color = voiceColorMap[line.voice] || "#8888aa";
              const isLocked = !!lockedLines?.[i];
              const qualityLine = qualityByIndex.get(i);
              const issues = qualityLine?.issues || [];
              const hasLongLineIssue = issues.some((x) => x.code === "long_line");
              const hasNormalizableSuggestion = !!(qualityLine?.suggestion && qualityLine.suggestion !== line.text);
              const hasErrors = issues.some((x) => x.severity === "error");
              const hasWarns = !hasErrors && issues.some((x) => x.severity === "warn");
              const rowOperationState =
                previewStatus[i] === "loading" || regenStatus[i] === "loading" || normStatus[i] === "loading"
                  ? "loading"
                  : previewStatus[i] === "error" || regenStatus[i] === "error" || normStatus[i] === "error"
                    ? "error"
                    : regenStatus[i] === "done" || normStatus[i] === "done"
                      ? "done"
                      : "";
              const grounding = String(line?.grounding || "document").trim().toLowerCase();
              const isExternalGrounding = grounding === "hybrid_external";
              return (
                <div key={i} className={`script-line ${isLocked ? "line-locked" : ""} ${hasErrors ? "line-error" : hasWarns ? "line-warn" : ""}`}>
                  <div className="script-line-head">
                    <span className="voice-tag" style={{ color }}>
                      {line.voice}
                    </span>
                    {isExternalGrounding && <span className="script-grounding-badge">вне документа</span>}
                    {!isEditing && (
                      <div className="script-line-controls">
                        <button
                          type="button"
                          className="secondary small line-control-btn"
                          onClick={() => handlePreviewLine(i, line)}
                          title="Сгенерировать и прослушать только эту реплику"
                          aria-label="Прослушать строку"
                        >
                          {previewStatus[i] === "loading" ? (
                            "…"
                          ) : previewStatus[i] === "playing" ? (
                            <ScriptActionIcon name="stop" />
                          ) : previewStatus[i] === "error" ? (
                            "!"
                          ) : (
                            <ScriptActionIcon name="play" />
                          )}
                        </button>
                        <button
                          type="button"
                          className="secondary small line-control-btn"
                          onClick={() => handleRegenerateLine(i)}
                          title={isLocked ? "Снимите lock, чтобы перегенерировать строку" : "Перегенерировать только эту реплику по контексту документа"}
                          disabled={isLocked || regenStatus[i] === "loading"}
                          aria-label="Перегенерировать строку"
                        >
                          {regenStatus[i] === "loading" ? "…" : <ScriptActionIcon name="regen" />}
                        </button>
                        <button
                          type="button"
                          className="secondary small line-control-btn"
                          onClick={() => toggleLineLock(i)}
                          title={isLocked ? "Снять lock с реплики" : "Зафиксировать реплику (защита от случайной правки/регенерации)"}
                          aria-label={isLocked ? "Снять lock" : "Поставить lock"}
                        >
                          <ScriptActionIcon name={isLocked ? "unlock" : "lock"} />
                        </button>
                        {rowOperationState && (
                          <span className={`line-op-status ${rowOperationState}`} title={`Статус операции: ${rowOperationState}`}>
                            {rowOperationState === "loading"
                              ? "loading"
                              : rowOperationState === "error"
                                ? "error"
                                : "done"}
                          </span>
                        )}
                      </div>
                    )}
                  </div>
                  {isEditing ? (
                    <textarea
                      className="line-textarea"
                      value={line.text}
                      onChange={(e) => handleDraftChange(i, e.target.value)}
                      disabled={editMode === "lines" && isLocked}
                      rows={Math.max(2, Math.min(8, line.text.split("\n").length))}
                    />
                  ) : (
                    <p className="line-text">{line.text}</p>
                  )}
                  {isEditing && editMode === "lines" && isLocked && (
                    <div className="line-issues">
                      <div className="line-issue issue-warn">Реплика зафиксирована (lock). Снимите lock для редактирования.</div>
                    </div>
                  )}
                  {!isEditing && issues.length > 0 && (
                    <div className="line-issues">
                      {issues.map((issue, idx) => (
                        <div key={idx} className={`line-issue ${issue.severity === "error" ? "issue-error" : "issue-warn"}`}>
                          {issue.message}
                        </div>
                      ))}
                      {qualityLine?.suggestion && qualityLine.suggestion !== line.text && (
                        <div className="line-suggestion">
                          Нормализованный вариант: {qualityLine.suggestion}
                        </div>
                      )}
                      {(hasNormalizableSuggestion || hasLongLineIssue) && (
                        <div className="line-fix-actions">
                          <button
                            type="button"
                            className="secondary small"
                            onClick={() => handleApplyNormalization(i, line, qualityLine)}
                            disabled={isLocked || normStatus[i] === "loading"}
                            title={
                              isLocked
                                ? "Снимите lock, чтобы применить нормализацию"
                                : hasLongLineIssue
                                ? "Применить нормализацию и попытаться разбить длинную реплику на две"
                                : "Применить нормализованный вариант к этой реплике"
                            }
                          >
                            {normStatus[i] === "loading"
                              ? "Применяю…"
                              : hasLongLineIssue
                                ? "Применить нормализацию и разбить"
                                : "Применить нормализацию"}
                          </button>
                          {normStatus[i] === "done" && <span className="text-muted">Готово</span>}
                          {normStatus[i] === "error" && <span className="text-muted">Ошибка</span>}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            });
          })()}
        </div>
      )}
      {!isEditing && (
        <div className="script-quality-summary">
          {loadingQuality
            ? "Проверяю текст для TTS…"
            : ttsQuality?.totals
            ? `TTS-проверка: ошибок ${ttsQuality.totals.errors}, предупреждений ${ttsQuality.totals.warnings}, реплик ${ttsQuality.totals.lines}.`
            : "TTS-проверка недоступна."}
        </div>
      )}
      {!isEditing && (
        <details className="script-insight-card">
          <summary>Главы и таймкоды (оценка)</summary>
          {loadingTimeline && <p className="text-muted">Считаю таймкоды…</p>}
          {!loadingTimeline && timeline?.chapters?.length > 0 && (
            <ul>
              {timeline.chapters.map((ch) => (
                <li key={ch.index}>
                  <strong>{ch.index}. {ch.title}</strong>{" "}
                  <span>
                    ({Math.floor(ch.start_sec)}s - {Math.floor(ch.end_sec)}s)
                  </span>
                </li>
              ))}
            </ul>
          )}
          {!loadingTimeline && (!timeline?.chapters || timeline.chapters.length === 0) && (
            <p className="text-muted">Нет данных по главам.</p>
          )}
        </details>
      )}
      {!isEditing && (
        <details className="script-insight-card">
          <summary>Метрики скрипта</summary>
          {loadingMetrics && <p className="text-muted">Считаю метрики…</p>}
          {!loadingMetrics && metrics?.quality && (
            <ul>
              <li>Слов: {metrics.totals.words}, уникальных: {metrics.totals.unique_words}</li>
              <li>Оценочная длительность: {Math.round(metrics.totals.duration_sec_estimate)} сек</li>
              <li>Темп: {metrics.quality.speech_rate_wpm} слов/мин ({metrics.quality.speech_rate_ok ? "норма" : "вне диапазона"})</li>
              <li>Лексическое разнообразие: {metrics.quality.lexical_diversity}</li>
              <li>Средняя длина предложения: {metrics.quality.avg_sentence_words} слов</li>
              <li>Повторяемость слов: {metrics.quality.repeated_share}</li>
            </ul>
          )}
          {!loadingMetrics && (!metrics || !metrics.quality) && (
            <p className="text-muted">Метрики недоступны.</p>
          )}
        </details>
      )}
      {!isEditing && (
        <details className="script-insight-card script-versions-card">
          <summary>Версии скрипта</summary>
          {loadingVersions ? (
            <p className="text-muted">Загружаю версии…</p>
          ) : versions.length === 0 ? (
            <p className="text-muted">Версии пока недоступны.</p>
          ) : (
            <>
              <div className="script-versions-head">
                <span className="script-meta-chip">
                  Текущая: {(versions.find((v) => String(v.version_id) === String(currentVersionId)) || {}).label || "—"}
                </span>
                <span className="script-meta-chip">Всего версий: {versions.length}</span>
              </div>
              <div className="script-versions-compare">
                <select value={leftVersionId} onChange={(e) => setLeftVersionId(e.target.value)} title="Левая версия для сравнения">
                  {versions.map((v) => (
                    <option key={`left-${v.version_id}`} value={v.version_id}>
                      {v.label} · {v.reason || "update"}
                    </option>
                  ))}
                </select>
                <span>vs</span>
                <select value={rightVersionId} onChange={(e) => setRightVersionId(e.target.value)} title="Правая версия для сравнения">
                  {versions.map((v) => (
                    <option key={`right-${v.version_id}`} value={v.version_id}>
                      {v.label} · {v.reason || "update"}
                    </option>
                  ))}
                </select>
                <button type="button" className="secondary small" onClick={handleCompareVersions} disabled={compareLoading}>
                  {compareLoading ? "Сравниваю…" : "Сравнить"}
                </button>
              </div>
              <div className="script-versions-list">
                {versions.map((v) => (
                  <div key={v.version_id} className={`script-version-row ${v.is_current ? "is-current" : ""}`}>
                    <div className="script-version-main">
                      <strong>{v.label}</strong>
                      <span className="text-muted">{v.reason || "update"}</span>
                      <span className="text-muted">{v.line_count} реплик</span>
                      <span className="text-muted">{v.created_at}</span>
                    </div>
                    <button
                      type="button"
                      className="secondary small"
                      disabled={v.is_current || restoreLoadingId === String(v.version_id)}
                      onClick={() => handleRestoreVersion(v.version_id)}
                      title={v.is_current ? "Эта версия уже текущая" : `Сделать ${v.label} текущей версией`}
                    >
                      {restoreLoadingId === String(v.version_id) ? "Восстанавливаю…" : v.is_current ? "Текущая" : "Восстановить"}
                    </button>
                  </div>
                ))}
              </div>
              {versionDiff?.diff && (
                <div className="script-version-diff">
                  <div className="script-version-diff-summary">
                    <strong>
                      {versionDiff?.left_version?.label || "v?"} → {versionDiff?.right_version?.label || "v?"}
                    </strong>
                    <span className="text-muted">
                      Изменено: {versionDiff.diff.changed}, добавлено: {versionDiff.diff.added}, удалено: {versionDiff.diff.removed}
                    </span>
                  </div>
                  {Array.isArray(versionDiff?.diff?.changes) && versionDiff.diff.changes.length > 0 && (
                    <ul>
                      {versionDiff.diff.changes.slice(0, 8).map((row) => (
                        <li key={`change-${row.line}`}>
                          <strong>Строка {row.line}:</strong> {row.change_type}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </>
          )}
        </details>
      )}
    </div>
  );
}

import React, { useState, useEffect, useRef } from "react";
import {
  listDocuments,
  getDocument,
  deleteDocument,
  runBatchJob,
  exportBatchBundle,
  downloadUrl,
  listProjects,
  createProject,
  updateProject,
  deleteProject,
  getProjectSettings,
  updateProjectSettings,
  getProjectNotebook,
  setProjectNotes,
  deleteProjectPin,
} from "../api/client";
import { buildDocumentStatus, buildSummaryStatus, buildScriptStatus } from "../utils/uiStatus";
import ConfirmDialog from "./ConfirmDialog";
import "./DocumentList.css";

const DOC_LIST_SELECTED_IDS_KEY = "document-list:selected-ids";
const DOC_LIST_ACTIVE_PROJECT_ID_KEY = "document-list:active-project-id";
const DOC_LIST_ACTIVE_PROJECT_NAME_KEY = "document-list:active-project-name";
const PROJECT_CHAT_MODES = ["default", "quote", "overview", "formulas"];
const PROJECT_CHAT_LENGTHS = ["short", "medium", "long"];
const PROJECT_CHAT_SCOPES = ["auto", "single", "collection"];
const PROJECT_SCRIPT_MODES = ["single_pass", "turn_taking"];

function normalizeProjectSettings(settings) {
  const src = settings && typeof settings === "object" ? settings : {};
  const chat = src.chat && typeof src.chat === "object" ? src.chat : {};
  const script = src.script && typeof src.script === "object" ? src.script : {};
  const minutesNum = Number(script.minutes);
  const minutes = Number.isFinite(minutesNum) ? Math.max(1, Math.min(60, Math.round(minutesNum))) : 5;
  const questionMode = PROJECT_CHAT_MODES.includes(String(chat.question_mode || "").toLowerCase())
    ? String(chat.question_mode).toLowerCase()
    : "default";
  const answerLength = PROJECT_CHAT_LENGTHS.includes(String(chat.answer_length || "").toLowerCase())
    ? String(chat.answer_length).toLowerCase()
    : "medium";
  const scope = PROJECT_CHAT_SCOPES.includes(String(chat.scope || "").toLowerCase())
    ? String(chat.scope).toLowerCase()
    : "auto";
  const generationMode = PROJECT_SCRIPT_MODES.includes(String(script.generation_mode || "").toLowerCase())
    ? String(script.generation_mode).toLowerCase()
    : "single_pass";
  const scenarioOptions = script.scenario_options && typeof script.scenario_options === "object"
    ? script.scenario_options
    : {};
  return {
    chat: {
      strict_sources: chat.strict_sources == null ? false : !!chat.strict_sources,
      use_summary_context: chat.use_summary_context == null ? false : !!chat.use_summary_context,
      question_mode: questionMode,
      answer_length: answerLength,
      scope,
    },
    script: {
      minutes,
      style: String(script.style || "conversational").trim() || "conversational",
      scenario: String(script.scenario || "classic_overview").trim() || "classic_overview",
      scenario_options: scenarioOptions,
      generation_mode: generationMode,
      focus: String(script.focus || ""),
      tts_friendly: script.tts_friendly == null ? true : !!script.tts_friendly,
    },
  };
}

function formatDate(createdAt) {
  if (!createdAt) return "";
  try {
    const d = new Date(createdAt);
    return d.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
  } catch {
    return createdAt;
  }
}

function statusLabel(doc) {
  const documentStatus = buildDocumentStatus({
    documentId: doc?.document_id,
    filename: doc?.filename,
    ingested: !!doc?.ingested,
    chunks: Number(doc?.chunks || 0),
    includeLabel: false,
  });
  const summaryStatus = buildSummaryStatus({
    isReady: !!doc?.has_summary,
  });
  const scriptStatus = buildScriptStatus({
    isReady: !!doc?.has_script,
    lines: Number(doc?.script_lines || 0),
  });
  const parts = [
    documentStatus.detail,
    `${summaryStatus.title.toLowerCase()}: ${summaryStatus.detail}`,
    `${scriptStatus.title.toLowerCase()}: ${scriptStatus.detail}`,
  ];
  return parts.join(" · ");
}

export default function DocumentList({
  documentListRefresh,
  projectNotebookRefresh = 0,
  onOpen,
  onError,
  onBatchJob,
  onSelectionChange,
  onProjectContextChange,
  participants = [],
  batchScriptSettings = null,
}) {
  const [docs, setDocs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [deleteConfirmId, setDeleteConfirmId] = useState(null);
  const [selected, setSelected] = useState(() => {
    try {
      const raw = localStorage.getItem(DOC_LIST_SELECTED_IDS_KEY);
      const arr = JSON.parse(raw || "[]");
      const next = {};
      for (const id of Array.isArray(arr) ? arr : []) {
        const v = String(id || "").trim();
        if (v) next[v] = true;
      }
      return next;
    } catch {
      return {};
    }
  });
  const [batchBusy, setBatchBusy] = useState(false);
  const [projects, setProjects] = useState([]);
  const [activeProjectId, setActiveProjectId] = useState(() => {
    try {
      return localStorage.getItem(DOC_LIST_ACTIVE_PROJECT_ID_KEY) || "";
    } catch {
      return "";
    }
  });
  const [projectName, setProjectName] = useState(() => {
    try {
      return localStorage.getItem(DOC_LIST_ACTIVE_PROJECT_NAME_KEY) || "";
    } catch {
      return "";
    }
  });
  const [projectBusy, setProjectBusy] = useState(false);
  const [notebookLoading, setNotebookLoading] = useState(false);
  const [notebookBusy, setNotebookBusy] = useState(false);
  const [projectNotes, setProjectNotesDraft] = useState("");
  const [projectNotesSaved, setProjectNotesSaved] = useState("");
  const [projectPins, setProjectPins] = useState([]);
  const [projectSettings, setProjectSettingsDraft] = useState(() => normalizeProjectSettings(null));
  const [projectSettingsSaved, setProjectSettingsSaved] = useState(() => normalizeProjectSettings(null));
  const [notebookExpanded, setNotebookExpanded] = useState(false);
  const [projectSettingsExpanded, setProjectSettingsExpanded] = useState(false);
  const [batchActionsExpanded, setBatchActionsExpanded] = useState(false);
  const projectApplySelectionRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    listDocuments()
      .then((list) => {
        if (!cancelled) setDocs(list || []);
      })
      .catch((e) => {
        if (!cancelled && onError) onError(e.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [documentListRefresh, onError]);

  useEffect(() => {
    let cancelled = false;
    listProjects()
      .then((rows) => {
        if (cancelled) return;
        setProjects(Array.isArray(rows) ? rows : []);
      })
      .catch((e) => {
        if (!cancelled) onError?.(e.message || "Не удалось загрузить наборы документов");
      });
    return () => {
      cancelled = true;
    };
  }, [documentListRefresh, onError]);

  useEffect(() => {
    let cancelled = false;
    const pid = String(activeProjectId || "").trim();
    if (!pid) {
      setProjectNotesDraft("");
      setProjectNotesSaved("");
      setProjectPins([]);
      const defaults = normalizeProjectSettings(null);
      setProjectSettingsDraft(defaults);
      setProjectSettingsSaved(defaults);
      setNotebookLoading(false);
      return () => {
        cancelled = true;
      };
    }
    setNotebookLoading(true);
    Promise.all([getProjectNotebook(pid), getProjectSettings(pid)])
      .then(([nb, settingsRes]) => {
        if (cancelled) return;
        const notes = String(nb?.notes || "");
        setProjectNotesDraft(notes);
        setProjectNotesSaved(notes);
        setProjectPins(Array.isArray(nb?.pinned_qas) ? nb.pinned_qas : []);
        const normalized = normalizeProjectSettings(settingsRes?.settings || null);
        setProjectSettingsDraft(normalized);
        setProjectSettingsSaved(normalized);
      })
      .catch((e) => {
        if (!cancelled) onError?.(e.message || "Не удалось загрузить заметки и закрепления набора документов.");
      })
      .finally(() => {
        if (!cancelled) setNotebookLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [activeProjectId, projectNotebookRefresh, documentListRefresh, onError]);

  async function handleOpen(id) {
    try {
      const doc = await getDocument(id);
      onOpen(doc);
    } catch (e) {
      if (onError) onError(e.message);
    }
  }

  async function handleConfirmDelete() {
    if (!deleteConfirmId) return;
    const id = deleteConfirmId;
    setDeleteConfirmId(null);
    try {
      await deleteDocument(id);
      setDocs((prev) => prev.filter((d) => d.document_id !== id));
      setSelected((prev) => {
        const next = { ...prev };
        delete next[id];
        return next;
      });
      try {
        const rows = await listProjects();
        setProjects(Array.isArray(rows) ? rows : []);
      } catch (_) {}
    } catch (e) {
      if (onError) onError(e.message);
    }
  }

  function toggleSelect(id, checked) {
    setSelected((prev) => ({ ...prev, [id]: checked }));
  }

  function selectedIds() {
    return docs.filter((d) => selected[d.document_id]).map((d) => d.document_id);
  }

  useEffect(() => {
    onSelectionChange?.(selectedIds());
  }, [selected, docs, onSelectionChange]);

  useEffect(() => {
    try {
      localStorage.setItem(DOC_LIST_SELECTED_IDS_KEY, JSON.stringify(selectedIds()));
    } catch (_) {}
  }, [selected, docs]);

  useEffect(() => {
    try {
      localStorage.setItem(DOC_LIST_ACTIVE_PROJECT_ID_KEY, activeProjectId || "");
      localStorage.setItem(DOC_LIST_ACTIVE_PROJECT_NAME_KEY, projectName || "");
    } catch (_) {}
  }, [activeProjectId, projectName]);

  useEffect(() => {
    const selectedDocIds = selectedIds();
    const active = projects.find((p) => p.project_id === activeProjectId) || null;
    onProjectContextChange?.(
      activeProjectId
        ? {
            project_id: activeProjectId,
            name: active?.name || projectName || "",
            document_ids: selectedDocIds,
            saved_document_ids: Array.isArray(active?.document_ids) ? active.document_ids : [],
            settings: normalizeProjectSettings(projectSettings),
            updated_at: active?.updated_at || null,
            is_dirty:
              !!active &&
              JSON.stringify([...(active.document_ids || [])].map(String).sort()) !==
                JSON.stringify([...selectedDocIds].map(String).sort()),
          }
        : null,
    );
  }, [activeProjectId, projectName, projects, selected, docs, projectSettings, onProjectContextChange]);

  function selectAll(checked) {
    const next = {};
    for (const d of docs) next[d.document_id] = checked;
    setSelected(next);
  }

  function applyProjectSelection(docIds) {
    const idSet = new Set((Array.isArray(docIds) ? docIds : []).map((x) => String(x || "").trim()).filter(Boolean));
    const next = {};
    for (const d of docs) next[d.document_id] = idSet.has(d.document_id);
    setSelected(next);
  }

  async function refreshProjectsKeepActive(preferredId = null) {
    const rows = await listProjects();
    const list = Array.isArray(rows) ? rows : [];
    setProjects(list);
    const targetId = preferredId ?? activeProjectId;
    if (targetId && !list.some((p) => p.project_id === targetId)) {
      setActiveProjectId("");
      setProjectName("");
    }
    return list;
  }

  async function loadActiveProjectNotebook(projectId) {
    const pid = String(projectId || "").trim();
    if (!pid) {
      setProjectNotesDraft("");
      setProjectNotesSaved("");
      setProjectPins([]);
      const defaults = normalizeProjectSettings(null);
      setProjectSettingsDraft(defaults);
      setProjectSettingsSaved(defaults);
      return;
    }
    setNotebookLoading(true);
    try {
      const [nb, settingsRes] = await Promise.all([
        getProjectNotebook(pid),
        getProjectSettings(pid),
      ]);
      const notes = String(nb?.notes || "");
      setProjectNotesDraft(notes);
      setProjectNotesSaved(notes);
      setProjectPins(Array.isArray(nb?.pinned_qas) ? nb.pinned_qas : []);
      const normalized = normalizeProjectSettings(settingsRes?.settings || null);
      setProjectSettingsDraft(normalized);
      setProjectSettingsSaved(normalized);
    } catch (e) {
      onError?.(e.message || "Не удалось загрузить заметки и закрепления набора документов.");
    } finally {
      setNotebookLoading(false);
    }
  }

  async function handleCreateProject() {
    const name = String(projectName || "").trim();
    if (!name) {
      onError?.("Укажите название набора документов.");
      return;
    }
    const ids = selectedIds();
    setProjectBusy(true);
    try {
      const row = await createProject({ name, document_ids: ids });
      setProjectName(row?.name || name);
      const pid = String(row?.project_id || "");
      setActiveProjectId(pid);
      await refreshProjectsKeepActive(pid);
      await loadActiveProjectNotebook(pid);
    } catch (e) {
      onError?.(e.message || "Не удалось создать набор документов.");
    } finally {
      setProjectBusy(false);
    }
  }

  async function handleSaveProjectSelection() {
    if (!activeProjectId) {
      onError?.("Сначала выберите набор документов.");
      return;
    }
    setProjectBusy(true);
    try {
      const body = { document_ids: selectedIds() };
      const name = String(projectName || "").trim();
      if (name) body.name = name;
      const row = await updateProject(activeProjectId, body);
      setProjectName(row?.name || projectName);
      await refreshProjectsKeepActive(activeProjectId);
    } catch (e) {
      onError?.(e.message || "Не удалось сохранить набор документов.");
    } finally {
      setProjectBusy(false);
    }
  }

  async function handleDeleteProject() {
    if (!activeProjectId) {
      onError?.("Выберите набор документов для удаления.");
      return;
    }
    setProjectBusy(true);
    try {
      await deleteProject(activeProjectId);
      setActiveProjectId("");
      setProjectName("");
      setProjectNotesDraft("");
      setProjectNotesSaved("");
      setProjectPins([]);
      const defaults = normalizeProjectSettings(null);
      setProjectSettingsDraft(defaults);
      setProjectSettingsSaved(defaults);
      await refreshProjectsKeepActive("");
    } catch (e) {
      onError?.(e.message || "Не удалось удалить набор документов.");
    } finally {
      setProjectBusy(false);
    }
  }

  function handlePickProject(projectId) {
    const nextId = String(projectId || "");
    setActiveProjectId(nextId);
    if (!nextId) {
      setProjectName("");
      return;
    }
    const row = projects.find((p) => p.project_id === nextId);
    if (row) {
      setProjectName(String(row.name || ""));
      projectApplySelectionRef.current = true;
      applyProjectSelection(row.document_ids || []);
      void loadActiveProjectNotebook(nextId);
    }
  }

  async function handleSaveProjectNotes() {
    const pid = String(activeProjectId || "").trim();
    if (!pid) {
      onError?.("Сначала выберите набор документов.");
      return;
    }
    setNotebookBusy(true);
    try {
      const row = await setProjectNotes(pid, projectNotes);
      const notes = String(row?.notes || "");
      setProjectNotesDraft(notes);
      setProjectNotesSaved(notes);
    } catch (e) {
      onError?.(e.message || "Не удалось сохранить заметки.");
    } finally {
      setNotebookBusy(false);
    }
  }

  async function handleSaveProjectSettings() {
    const pid = String(activeProjectId || "").trim();
    if (!pid) {
      onError?.("Сначала выберите набор документов.");
      return;
    }
    setNotebookBusy(true);
    try {
      const normalized = normalizeProjectSettings(projectSettings);
      const row = await updateProjectSettings(pid, normalized);
      const savedSettings = normalizeProjectSettings(row?.settings || normalized);
      setProjectSettingsDraft(savedSettings);
      setProjectSettingsSaved(savedSettings);
      await refreshProjectsKeepActive(pid);
    } catch (e) {
      onError?.(e.message || "Не удалось сохранить настройки набора документов.");
    } finally {
      setNotebookBusy(false);
    }
  }

  async function handleDeleteNotebookPin(pinId) {
    const pid = String(activeProjectId || "").trim();
    const pinKey = String(pinId || "").trim();
    if (!pid || !pinKey) return;
    setNotebookBusy(true);
    try {
      await deleteProjectPin(pid, pinKey);
      setProjectPins((prev) => prev.filter((p) => String(p?.pin_id || "") !== pinKey));
    } catch (e) {
      onError?.(e.message || "Не удалось удалить закреплённый Q&A.");
    } finally {
      setNotebookBusy(false);
    }
  }

  useEffect(() => {
    if (!docs.length || !activeProjectId || !projects.length) return;
    const row = projects.find((p) => p.project_id === activeProjectId);
    if (!row) return;
    // On remount/reload restore selection from active project if current selection is empty
    // or if previous render intentionally applied project selection.
    const currentSelected = selectedIds();
    if (projectApplySelectionRef.current || currentSelected.length === 0) {
      projectApplySelectionRef.current = false;
      applyProjectSelection(row.document_ids || []);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docs.length, activeProjectId, projects]);

  async function handleRunBatch(mode) {
    const ids = selectedIds();
    if (!ids.length) {
      onError?.("Выберите хотя бы один документ для пакетной генерации.");
      return;
    }
    setBatchBusy(true);
    try {
      const voices = (Array.isArray(participants) ? participants : [])
        .map((p) => String((p && p.name) || "").trim())
        .filter(Boolean);
      const scriptCfg = batchScriptSettings || {};
      const res = await runBatchJob({
        document_ids: ids,
        mode,
        minutes: Math.min(60, Math.max(1, Number(scriptCfg.minutes) || 5)),
        style: String(scriptCfg.style || "conversational"),
        focus: String(scriptCfg.focus || "").trim() || undefined,
        voices: voices.length ? voices : ["Игорь", "Аня", "Максим"],
        scenario: String(scriptCfg.scenario || "classic_overview"),
        scenario_options:
          scriptCfg.scenario_options && typeof scriptCfg.scenario_options === "object"
            ? scriptCfg.scenario_options
            : {},
        generation_mode: String(scriptCfg.generation_mode || "single_pass"),
        role_llm_map:
          scriptCfg.role_llm_map && typeof scriptCfg.role_llm_map === "object"
            ? scriptCfg.role_llm_map
            : undefined,
        tts_friendly: scriptCfg.tts_friendly == null ? true : !!scriptCfg.tts_friendly,
      });
      onBatchJob?.(res.job_id);
    } catch (e) {
      onError?.(e.message || "Не удалось запустить пакетную генерацию.");
    } finally {
      setBatchBusy(false);
    }
  }

  async function handleExportSelected() {
    const ids = selectedIds();
    if (!ids.length) {
      onError?.("Выберите хотя бы один документ для пакетного экспорта.");
      return;
    }
    setBatchBusy(true);
    try {
      const res = await exportBatchBundle(ids);
      if (res && res.filename) {
        const a = document.createElement("a");
        a.href = downloadUrl(res.filename);
        a.download = res.filename;
        a.click();
      }
    } catch (e) {
      onError?.(e.message || "Не удалось собрать пакетный архив.");
    } finally {
      setBatchBusy(false);
    }
  }

  const hasActiveProject = !!String(activeProjectId || "").trim();
  const notesDirty = String(projectNotes || "") !== String(projectNotesSaved || "");
  const settingsDirty = JSON.stringify(normalizeProjectSettings(projectSettings)) !== JSON.stringify(normalizeProjectSettings(projectSettingsSaved));
  const hasNotebookContent = !!String(projectNotesSaved || "").trim() || projectPins.length > 0;

  useEffect(() => {
    if (hasActiveProject && hasNotebookContent) {
      setNotebookExpanded(true);
    }
  }, [hasActiveProject, hasNotebookContent]);

  useEffect(() => {
    if (settingsDirty) {
      setProjectSettingsExpanded(true);
      setNotebookExpanded(true);
    }
  }, [settingsDirty]);

  if (loading) {
    return (
      <div className="card document-list">
        <p className="text-muted">Загрузка списка документов…</p>
      </div>
    );
  }

  if (docs.length === 0) {
    return null;
  }

  return (
    <>
      <div className="card document-list">
        <h3 className="document-list-title">Мои документы</h3>
        <div className="document-list-projects">
          <div className="document-list-projects-row">
            <select
              value={activeProjectId}
              onChange={(e) => handlePickProject(e.target.value)}
              disabled={projectBusy}
              title="Выбрать сохранённый набор документов"
            >
              <option value="">Набор документов не выбран</option>
              {projects.map((p) => (
                <option key={p.project_id} value={p.project_id}>
                  {p.name} ({Array.isArray(p.document_ids) ? p.document_ids.length : p.document_count || 0})
                </option>
              ))}
            </select>
            <button
              type="button"
              className="secondary"
              onClick={() => handlePickProject(activeProjectId)}
              disabled={!activeProjectId || projectBusy}
              title="Применить выбор документов из набора"
            >
              Применить
            </button>
          </div>
          <div className="document-list-projects-row">
            <input
              type="text"
              value={projectName}
              onChange={(e) => setProjectName(e.target.value)}
              placeholder="Название набора документов"
              disabled={projectBusy}
            />
            <button
              type="button"
              className="secondary"
              onClick={handleCreateProject}
              disabled={projectBusy}
              title="Создать набор документов из выбранных файлов"
            >
              {projectBusy ? "Сохранение…" : "Создать набор"}
            </button>
          </div>
          <div className="document-list-projects-row">
            <button
              type="button"
              className="secondary"
              onClick={handleSaveProjectSelection}
              disabled={!activeProjectId || projectBusy}
              title="Сохранить текущий выбор документов в выбранный набор"
            >
              Сохранить выбор в набор
            </button>
            <button
              type="button"
              className="secondary document-list-delete-btn"
              onClick={handleDeleteProject}
              disabled={!activeProjectId || projectBusy}
              title="Удалить выбранный набор документов"
            >
              Удалить набор
            </button>
          </div>
        </div>
        <details
          className={`document-list-panel collapsible-panel document-list-notebook${hasActiveProject ? "" : " is-disabled"}`}
          open={notebookExpanded}
          onToggle={(e) => setNotebookExpanded(e.currentTarget.open)}
        >
          <summary>
            Заметки и закрепления
            <span className="document-list-summary-meta">
              {hasActiveProject ? (notebookLoading ? "загрузка…" : `${projectPins.length} закрепл.`) : "набор не выбран"}
            </span>
          </summary>
          {hasActiveProject ? (
            <div className="document-list-panel-body">
              <label className="document-list-notes-label">
                <span>Заметки</span>
                <textarea
                  value={projectNotes}
                  onChange={(e) => setProjectNotesDraft(e.target.value)}
                  rows={4}
                  placeholder="Ключевые выводы, идеи и TODO по набору документов"
                  disabled={notebookBusy || notebookLoading}
                />
              </label>
              <div className="document-list-notebook-actions">
                <button
                  type="button"
                  className="secondary"
                  onClick={handleSaveProjectNotes}
                  disabled={notebookBusy || notebookLoading || !notesDirty}
                    title="Сохранить заметки по набору документов"
                >
                  {notebookBusy ? "Сохранение…" : "Сохранить заметки"}
                </button>
              </div>
              <details
                className="document-list-project-settings collapsible-panel"
                open={projectSettingsExpanded}
                onToggle={(e) => setProjectSettingsExpanded(e.currentTarget.open)}
              >
                <summary>
                  Настройки набора
                  <span className="document-list-summary-meta">
                    {settingsDirty ? "есть изменения" : "по умолчанию"}
                  </span>
                </summary>
                <div className="document-list-panel-body">
                <div className="document-list-project-settings-grid">
                  <label>
                    <span>Режим ответа</span>
                    <select
                      value={projectSettings.chat.question_mode}
                      onChange={(e) =>
                        setProjectSettingsDraft((prev) => normalizeProjectSettings({
                          ...prev,
                          chat: { ...prev.chat, question_mode: e.target.value },
                        }))
                      }
                      disabled={notebookBusy || notebookLoading}
                    >
                      <option value="default">Баланс</option>
                      <option value="quote">С цитатами</option>
                      <option value="overview">Обзор</option>
                      <option value="formulas">Формулы</option>
                    </select>
                  </label>
                  <label>
                    <span>Длина ответа</span>
                    <select
                      value={projectSettings.chat.answer_length}
                      onChange={(e) =>
                        setProjectSettingsDraft((prev) => normalizeProjectSettings({
                          ...prev,
                          chat: { ...prev.chat, answer_length: e.target.value },
                        }))
                      }
                      disabled={notebookBusy || notebookLoading}
                    >
                      <option value="short">Короткий</option>
                      <option value="medium">Средний</option>
                      <option value="long">Длинный</option>
                    </select>
                  </label>
                  <label>
                    <span>Контекст чата</span>
                    <select
                      value={projectSettings.chat.scope}
                      onChange={(e) =>
                        setProjectSettingsDraft((prev) => normalizeProjectSettings({
                          ...prev,
                          chat: { ...prev.chat, scope: e.target.value },
                        }))
                      }
                      disabled={notebookBusy || notebookLoading}
                    >
                      <option value="auto">Авто</option>
                      <option value="single">Один документ</option>
                      <option value="collection">Набор документов</option>
                    </select>
                  </label>
                  <label>
                    <span>Длительность (мин)</span>
                    <input
                      type="number"
                      min={1}
                      max={60}
                      value={projectSettings.script.minutes}
                      onChange={(e) =>
                        setProjectSettingsDraft((prev) => normalizeProjectSettings({
                          ...prev,
                          script: { ...prev.script, minutes: Number(e.target.value) || 5 },
                        }))
                      }
                      disabled={notebookBusy || notebookLoading}
                    />
                  </label>
                  <label>
                    <span>Тон речи</span>
                    <input
                      type="text"
                      value={projectSettings.script.style}
                      onChange={(e) =>
                        setProjectSettingsDraft((prev) => normalizeProjectSettings({
                          ...prev,
                          script: { ...prev.script, style: e.target.value },
                        }))
                      }
                      disabled={notebookBusy || notebookLoading}
                    />
                  </label>
                  <label>
                    <span>Формат разговора</span>
                    <input
                      type="text"
                      value={projectSettings.script.scenario}
                      onChange={(e) =>
                        setProjectSettingsDraft((prev) => normalizeProjectSettings({
                          ...prev,
                          script: { ...prev.script, scenario: e.target.value },
                        }))
                      }
                      disabled={notebookBusy || notebookLoading}
                    />
                  </label>
                  <label>
                    <span>Режим генерации</span>
                    <select
                      value={projectSettings.script.generation_mode}
                      onChange={(e) =>
                        setProjectSettingsDraft((prev) => normalizeProjectSettings({
                          ...prev,
                          script: { ...prev.script, generation_mode: e.target.value },
                        }))
                      }
                      disabled={notebookBusy || notebookLoading}
                    >
                      <option value="single_pass">Один проход</option>
                      <option value="turn_taking">Пошагово по ролям</option>
                    </select>
                  </label>
                  <label className="document-list-project-settings-toggle">
                    <input
                      type="checkbox"
                      checked={!!projectSettings.chat.strict_sources}
                      onChange={(e) =>
                        setProjectSettingsDraft((prev) => normalizeProjectSettings({
                          ...prev,
                          chat: { ...prev.chat, strict_sources: e.target.checked },
                        }))
                      }
                      disabled={notebookBusy || notebookLoading}
                    />
                    <span>Строго по источникам</span>
                  </label>
                  <label className="document-list-project-settings-toggle">
                    <input
                      type="checkbox"
                      checked={!!projectSettings.chat.use_summary_context}
                      onChange={(e) =>
                        setProjectSettingsDraft((prev) => normalizeProjectSettings({
                          ...prev,
                          chat: { ...prev.chat, use_summary_context: e.target.checked },
                        }))
                      }
                      disabled={notebookBusy || notebookLoading}
                    />
                    <span>Использовать саммари как доп. контекст</span>
                  </label>
                  <label className="document-list-project-settings-toggle">
                    <input
                      type="checkbox"
                      checked={!!projectSettings.script.tts_friendly}
                      onChange={(e) =>
                        setProjectSettingsDraft((prev) => normalizeProjectSettings({
                          ...prev,
                          script: { ...prev.script, tts_friendly: e.target.checked },
                        }))
                      }
                      disabled={notebookBusy || notebookLoading}
                    />
                    <span>TTS-оптимизация: ударения, транскрипция и числа словами</span>
                  </label>
                </div>
                <label className="document-list-notes-label">
                  <span>Фокус выпуска (опц.)</span>
                  <textarea
                    value={projectSettings.script.focus}
                    onChange={(e) =>
                      setProjectSettingsDraft((prev) => normalizeProjectSettings({
                        ...prev,
                        script: { ...prev.script, focus: e.target.value },
                      }))
                    }
                    rows={2}
                    placeholder="Короткий фокус для генерации скрипта"
                    disabled={notebookBusy || notebookLoading}
                  />
                </label>
                <div className="document-list-notebook-actions">
                  <button
                    type="button"
                    className="secondary"
                    onClick={handleSaveProjectSettings}
                    disabled={notebookBusy || notebookLoading || !settingsDirty}
                    title="Сохранить проектные настройки по умолчанию"
                  >
                    {notebookBusy ? "Сохранение…" : "Сохранить настройки проекта"}
                  </button>
                </div>
                </div>
              </details>
              <div className="document-list-pins">
                <div className="document-list-pins-title">Закреплённые Q&A</div>
                {projectPins.length === 0 ? (
                  <div className="text-muted">Пока пусто. Закрепите ответ из чата кнопкой «В заметки».</div>
                ) : (
                  <ul className="document-list-pins-ul">
                    {projectPins.slice(0, 30).map((pin) => (
                      <li key={pin.pin_id} className="document-list-pin-item">
                        <div className="document-list-pin-meta">
                          {pin.mode ? <span>{pin.mode}</span> : <span>qa</span>}
                          <span>{formatDate(pin.updated_at || pin.created_at)}</span>
                        </div>
                        {pin.question ? <div className="document-list-pin-question">{pin.question}</div> : null}
                        <div className="document-list-pin-answer">{pin.answer}</div>
                        {Array.isArray(pin.citations) && pin.citations.length > 0 ? (
                          <div className="document-list-pin-citations">
                            Источники: {pin.citations.length}
                          </div>
                        ) : null}
                        <div className="document-list-pin-actions">
                          <button
                            type="button"
                            className="secondary small document-list-delete-btn"
                            onClick={() => { void handleDeleteNotebookPin(pin.pin_id); }}
                            disabled={notebookBusy}
                            title="Удалить закреплённый Q&A"
                          >
                            Удалить
                          </button>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          ) : (
            <div className="text-muted">Выберите набор документов выше, чтобы работать с заметками и закреплёнными ответами.</div>
          )}
        </details>
        <details
          className="document-list-panel collapsible-panel"
          open={batchActionsExpanded}
          onToggle={(e) => setBatchActionsExpanded(e.currentTarget.open)}
        >
          <summary>
            Пакетные действия
            <span className="document-list-summary-meta">
              {selectedIds().length ? `${selectedIds().length} выбрано` : "ничего не выбрано"}
            </span>
          </summary>
          <div className="document-list-panel-body">
            <div className="document-list-batch-actions">
              <label className="document-list-select-all">
                <input
                  type="checkbox"
                  checked={docs.length > 0 && docs.every((d) => selected[d.document_id])}
                  onChange={(e) => selectAll(e.target.checked)}
                />
                Выбрать все
              </label>
              <button
                type="button"
                className="secondary"
                onClick={() => handleRunBatch("audio")}
                disabled={batchBusy}
                title="Сгенерировать аудио для выбранных документов (по существующим скриптам)"
              >
                {batchBusy ? "Запуск…" : "Пакет: аудио"}
              </button>
              <button
                type="button"
                className="secondary"
                onClick={() => handleRunBatch("script_audio")}
                disabled={batchBusy}
                title="Сгенерировать скрипт и затем аудио для выбранных документов"
              >
                {batchBusy ? "Запуск…" : "Пакет: скрипт + аудио"}
              </button>
              <button
                type="button"
                className="secondary"
                onClick={handleExportSelected}
                disabled={batchBusy}
                title="Скачать ZIP-архив по выбранным документам"
              >
                {batchBusy ? "Сборка…" : "Пакет: экспорт ZIP"}
              </button>
            </div>
          </div>
        </details>
        <ul className="document-list-ul">
          {docs.map((d) => (
            <li key={d.document_id} className="document-list-item">
              <div className="document-list-item-info">
                <label className="document-select">
                  <input
                    type="checkbox"
                    checked={!!selected[d.document_id]}
                    onChange={(e) => toggleSelect(d.document_id, e.target.checked)}
                  />
                </label>
                <span className="document-list-filename">{d.filename}</span>
                <span className="document-list-meta">
                  {formatDate(d.created_at)} · {statusLabel(d)}
                </span>
              </div>
              <div className="document-list-item-actions">
                <button type="button" className="secondary" onClick={() => handleOpen(d.document_id)} title="Открыть документ">
                  Открыть
                </button>
                <button type="button" className="secondary document-list-delete-btn" onClick={() => setDeleteConfirmId(d.document_id)} title="Удалить документ из базы">
                  Удалить
                </button>
              </div>
            </li>
          ))}
        </ul>
      </div>
      <ConfirmDialog
        open={!!deleteConfirmId}
        message="Удалить документ из базы? Файлы и индекс будут удалены."
        cancelLabel="Отмена"
        confirmLabel="Удалить"
        danger
        onConfirm={handleConfirmDelete}
        onCancel={() => setDeleteConfirmId(null)}
      />
    </>
  );
}

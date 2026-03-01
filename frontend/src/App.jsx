import React, { useState, useCallback, useRef, useEffect } from "react";
import UploadPanel from "./components/UploadPanel";
import DocumentList from "./components/DocumentList";
import ActionBar from "./components/ActionBar";
import SummaryPanel from "./components/SummaryPanel";
import ScriptPanel from "./components/ScriptPanel";
import JobPanel from "./components/JobPanel";
import SettingsPanel from "./components/SettingsPanel";
import ChatPanel from "./components/ChatPanel";
import SourceViewerPane from "./components/SourceViewerPane";
import { addProjectPin, deleteDocument, getDocument, getRoleLlmSettings, ingest } from "./api/client";
import { buildDocumentStatus, buildSummaryStatus, buildScriptStatus } from "./utils/uiStatus";
import "./App.css";

const DEFAULT_PARTICIPANTS = [
  { role: "host", name: "Игорь" },
  { role: "guest1", name: "Аня" },
  { role: "guest2", name: "Максим" },
];

function normalizeParticipants(list) {
  const raw = Array.isArray(list) ? list : [];
  const source = raw.length ? raw : DEFAULT_PARTICIPANTS;
  const out = [];
  const usedRoles = new Set();
  const usedNames = new Set();
  for (let i = 0; i < source.length; i += 1) {
    const row = source[i] || {};
    let role = String(row.role || "").trim() || DEFAULT_PARTICIPANTS[i]?.role || `role_${i + 1}`;
    let name = String(row.name || "").trim() || DEFAULT_PARTICIPANTS[i]?.name || `Спикер ${i + 1}`;
    let roleN = 2;
    while (usedRoles.has(role.toLowerCase())) {
      role = `${role}_${roleN}`;
      roleN += 1;
    }
    let nameN = 2;
    while (usedNames.has(name.toLowerCase())) {
      name = `${name} ${nameN}`;
      nameN += 1;
    }
    usedRoles.add(role.toLowerCase());
    usedNames.add(name.toLowerCase());
    out.push({ role, name });
  }
  return out.length ? out : DEFAULT_PARTICIPANTS;
}

function syncParticipantsToScenarioRoles(list, scenarioRoles) {
  const roles = Array.isArray(scenarioRoles)
    ? scenarioRoles.map((x) => String(x || "").trim()).filter(Boolean)
    : [];
  if (!roles.length) return normalizeParticipants(list);
  const current = normalizeParticipants(list);
  const next = roles.map((role, index) => ({
    role,
    name: current[index]?.name || DEFAULT_PARTICIPANTS[index]?.name || `Спикер ${index + 1}`,
  }));
  return normalizeParticipants(next);
}

function sameParticipants(a, b) {
  const left = normalizeParticipants(a);
  const right = normalizeParticipants(b);
  if (left.length !== right.length) return false;
  for (let i = 0; i < left.length; i += 1) {
    if (left[i]?.role !== right[i]?.role || left[i]?.name !== right[i]?.name) return false;
  }
  return true;
}

const NETWORK_ERROR_PATTERNS = [
  "load failed",
  "failed to fetch",
  "networkerror when attempting to fetch resource",
];

const CANCELLATION_ERROR_PATTERNS = [
  "aborterror",
  "the user aborted a request",
  "signal is aborted",
  "request aborted",
];

function normalizeUiErrorMessage(message) {
  const msg = String(message || "").trim();
  if (!msg) return "";
  const lowered = msg.toLowerCase();
  if (CANCELLATION_ERROR_PATTERNS.some((pattern) => lowered.includes(pattern))) {
    return "";
  }
  if (NETWORK_ERROR_PATTERNS.some((pattern) => lowered.includes(pattern))) {
    return "Сетевая ошибка: не удалось загрузить данные. Проверьте подключение и повторите.";
  }
  return msg;
}

export default function App() {
  const [topbarOffset, setTopbarOffset] = useState(116);
  const [activeProjectContext, setActiveProjectContext] = useState(() => {
    try {
      const raw = localStorage.getItem("active-project-context");
      const parsed = raw ? JSON.parse(raw) : null;
      return parsed && typeof parsed === "object" ? parsed : null;
    } catch (_) {
      return null;
    }
  });
  const [documentId, setDocumentId] = useState(null);
  const [filename, setFilename] = useState("");
  const [ingested, setIngested] = useState(false);
  const [chunks, setChunks] = useState(0);
  const [summary, setSummary] = useState(null);
  const [sources, setSources] = useState([]);
  const [script, setScript] = useState(null);
  const [audioJobId, setAudioJobId] = useState(null);
  const [batchJobId, setBatchJobId] = useState(null);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [showSettings, setShowSettings] = useState(false);
  const [participants, setParticipants] = useState(DEFAULT_PARTICIPANTS);
  const [roleLlmMap, setRoleLlmMap] = useState({});
  const [podcastScriptSettings, setPodcastScriptSettings] = useState({
    minutes: 5,
    style: "conversational",
    scenario: "classic_overview",
    scenario_options: {},
    generation_mode: "single_pass",
    tts_friendly: true,
    knowledge_mode: "document_only",
  });
  const [documentListRefresh, setDocumentListRefresh] = useState(0);
  const [sourceSelectedDocumentIds, setSourceSelectedDocumentIds] = useState([]);
  const [streamingSummary, setStreamingSummary] = useState(null);
  const [streamingScript, setStreamingScript] = useState(null);
  const [showDocumentListWhenActive, setShowDocumentListWhenActive] = useState(false);
  const [projectNotebookRefresh, setProjectNotebookRefresh] = useState(0);
  const [showHelpModal, setShowHelpModal] = useState(false);
  const [helpTab, setHelpTab] = useState("start");
  const [theme, setTheme] = useState("night");
  const [summaryExpanded, setSummaryExpanded] = useState(false);
  const [scriptExpanded, setScriptExpanded] = useState(false);
  const [showSources, setShowSources] = useState(() => {
    try {
      const raw = localStorage.getItem("show-sources-column");
      if (raw === null) return true;
      return raw === "1";
    } catch (_) {
      return true;
    }
  });
  const [showStudio, setShowStudio] = useState(() => {
    try {
      const raw = localStorage.getItem("show-studio-column");
      if (raw === null) return true;
      return raw === "1";
    } catch (_) {
      return true;
    }
  });
  const [liteMode, setLiteMode] = useState(() => {
    try {
      const raw = localStorage.getItem("ui-lite-mode");
      if (raw === null) return true;
      return raw === "1";
    } catch (_) {
      return true;
    }
  });
  const [viewerCitation, setViewerCitation] = useState(null);
  const [viewerLoading, setViewerLoading] = useState(false);
  const [viewerError, setViewerError] = useState(null);
  const [autoIngesting, setAutoIngesting] = useState(false);
  function isIgnorableErrorMessage(msg) {
    const t = String(normalizeUiErrorMessage(msg) || "").toLowerCase().trim();
    if (!t) return true;
    if (t === "not found") return true;
    if (t.includes("\"detail\":\"not found\"")) return true;
    if (t.includes("{'detail':'not found'}")) return true;
    return false;
  }

  const hasVisibleError = !!error && !isIgnorableErrorMessage(error);
  const topbarRef = useRef(null);
  const chatRef = useRef(null);
  const actionRef = useRef(null);
  const summaryRef = useRef(null);
  const scriptRef = useRef(null);
  const studioSourceRef = useRef(null);
  const settingsRef = useRef(null);
  const autoIngestSeqRef = useRef(0);
  const handleUiError = useCallback((message) => {
    const normalized = normalizeUiErrorMessage(message);
    if (!normalized) {
      setError(null);
      return;
    }
    setError(normalized);
  }, []);

  function scrollToRef(ref) {
    if (!ref?.current) return;
    const node = ref.current;
    const rect = node.getBoundingClientRect();
    const absoluteTop = window.scrollY + rect.top;
    const offset = Math.max(0, topbarOffset + 10);
    const nextTop = Math.max(0, absoluteTop - offset);
    window.scrollTo({ top: nextTop, behavior: "smooth" });
  }

  function resolveCitationKey(citation) {
    const c = citation && typeof citation === "object" ? citation : {};
    const evidenceId = String(c.evidence_id || "").trim();
    if (evidenceId) return `ev:${evidenceId}`;
    const anchorId = String(c.anchor_id || "").trim();
    if (anchorId) return `a:${anchorId}`;
    const doc = String(c.document_id || "").trim();
    const chunk = String(c.chunk_id || "").trim();
    const idx = c.chunk_index != null ? String(c.chunk_index) : "na";
    return `legacy:${doc}/${chunk}:${idx}`;
  }

  const summaryChars = String(streamingSummary ?? summary ?? "").trim().length;
  const summarySourceCount = Array.isArray(sources) ? sources.length : 0;
  const scriptLineCount = Array.isArray(script) ? script.length : 0;
  const documentStatus = buildDocumentStatus({
    documentId,
    filename,
    ingested,
    chunks,
    autoIngesting,
  });
  const summaryStatusItem = buildSummaryStatus({
    summary,
    streamingSummary,
    chars: summaryChars,
    sourcesCount: summarySourceCount,
  });
  const scriptStatusItem = buildScriptStatus({
    script,
    streamingScript,
    lines: scriptLineCount,
  });
  const topbarDocumentStatus = `${documentStatus.title}: ${documentStatus.detail}`;
  const topbarSummaryStatus = `${summaryStatusItem.title}: ${summaryStatusItem.detail}`;
  const topbarScriptStatus = `${scriptStatusItem.title}: ${scriptStatusItem.detail}`;
  const viewerSubheadToneClass = viewerError ? "state-error" : ((viewerLoading || autoIngesting) ? "state-loading" : "");
  const viewerSubheadLite = !documentId
    ? "Добавьте документ"
    : viewerError
      ? "Ошибка просмотра"
      : viewerLoading
        ? "Загрузка источника…"
        : autoIngesting
          ? "Автоиндексация…"
          : viewerCitation?.document_id
            ? `${viewerCitation.document_id}/${viewerCitation.chunk_id}`
            : `${documentId} · полный текст`;
  const viewerSubheadFull = !documentId
    ? "Выберите документ"
    : viewerError
      ? "Ошибка просмотра"
      : viewerLoading
        ? "Загрузка…"
        : autoIngesting
          ? "Автоиндексация…"
          : viewerCitation?.document_id
            ? `${viewerCitation.document_id}/${viewerCitation.chunk_id}`
            : `${documentId} · полный текст`;

  const handleOpenSourceCitation = useCallback((citation) => {
    if (!citation || typeof citation !== "object") {
      setViewerCitation((prev) => (prev === null ? prev : null));
      return;
    }
    setViewerCitation((prev) => {
      if (!prev || typeof prev !== "object") return citation;
      const prevKey = resolveCitationKey(prev);
      const nextKey = resolveCitationKey(citation);
      if (prevKey && nextKey && prevKey === nextKey) return prev;
      return citation;
    });
  }, []);

  const handleScenarioRolesChange = useCallback((scenarioRoles) => {
    setParticipants((prev) => {
      const next = syncParticipantsToScenarioRoles(prev, scenarioRoles);
      return sameParticipants(prev, next) ? prev : next;
    });
  }, []);

  function handleReset() {
    autoIngestSeqRef.current += 1;
    setDocumentId(null);
    setFilename("");
    setIngested(false);
    setChunks(0);
    setSummary(null);
    setSources([]);
    setScript(null);
    setAudioJobId(null);
    setBatchJobId(null);
    setError(null);
    setNotice(null);
    setAutoIngesting(false);
    setViewerCitation(null);
    setViewerError(null);
    setViewerLoading(false);
    setSummaryExpanded(false);
    setScriptExpanded(false);
  }

  useEffect(() => {
    const saved = localStorage.getItem("ui-theme");
    if (saved === "day" || saved === "night") {
      setTheme(saved);
      document.documentElement.setAttribute("data-theme", saved);
      return;
    }
    document.documentElement.setAttribute("data-theme", "night");
  }, []);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("ui-theme", theme);
  }, [theme]);

  useEffect(() => {
    const node = topbarRef.current;
    if (!node) return undefined;

    const updateTopbarOffset = () => {
      const height = Math.ceil(node.getBoundingClientRect().height);
      setTopbarOffset(height + 24);
    };

    updateTopbarOffset();

    let observer = null;
    if (typeof ResizeObserver !== "undefined") {
      observer = new ResizeObserver(() => updateTopbarOffset());
      observer.observe(node);
    }
    window.addEventListener("resize", updateTopbarOffset);
    return () => {
      window.removeEventListener("resize", updateTopbarOffset);
      if (observer) observer.disconnect();
    };
  }, []);

  useEffect(() => {
    function onKeyDown(e) {
      if (e.key === "Escape") setShowHelpModal(false);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  useEffect(() => {
    let cancelled = false;
    getRoleLlmSettings()
      .then((res) => {
        if (cancelled) return;
        const next = res && typeof res === "object" && res.role_llm_map && typeof res.role_llm_map === "object"
          ? res.role_llm_map
          : {};
        setRoleLlmMap(next);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (summary || streamingSummary) setSummaryExpanded(true);
  }, [summary, streamingSummary]);

  useEffect(() => {
    if (script || streamingScript) setScriptExpanded(true);
  }, [script, streamingScript]);

  useEffect(() => {
    try {
      localStorage.setItem("show-studio-column", showStudio ? "1" : "0");
    } catch (_) {}
  }, [showStudio]);

  useEffect(() => {
    try {
      localStorage.setItem("show-sources-column", showSources ? "1" : "0");
    } catch (_) {}
  }, [showSources]);

  useEffect(() => {
    try {
      localStorage.setItem("ui-lite-mode", liteMode ? "1" : "0");
    } catch (_) {}
  }, [liteMode]);

  useEffect(() => {
    try {
      if (activeProjectContext && typeof activeProjectContext === "object") {
        localStorage.setItem("active-project-context", JSON.stringify(activeProjectContext));
      } else {
        localStorage.removeItem("active-project-context");
      }
    } catch (_) {}
  }, [activeProjectContext]);

  const handleOpenDocument = useCallback((doc) => {
    autoIngestSeqRef.current += 1;
    setDocumentId(doc.document_id);
    setFilename(doc.filename || "");
    setIngested(!!doc.ingested);
    setChunks(doc.chunks || 0);
    setSummary(doc.summary || null);
    setSources(doc.sources || []);
    setScript(doc.script || null);
    setAudioJobId(null);
    setBatchJobId(null);
    setError(null);
    setNotice(null);
    setShowDocumentListWhenActive(false);
    setAutoIngesting(false);
    setViewerCitation(null);
    setViewerError(null);
    setSummaryExpanded(!!doc.summary);
    setScriptExpanded(!!doc.script);
  }, []);

  async function handleDeleteCurrentDocument() {
    if (!documentId) return;
    try {
      await deleteDocument(documentId);
      handleReset();
      setDocumentListRefresh((r) => r + 1);
    } catch (e) {
      handleUiError(e?.message || "Не удалось удалить документ.");
    }
  }

  function handleScriptOnlyImported(doc) {
    autoIngestSeqRef.current += 1;
    setDocumentId(doc.document_id);
    setFilename(doc.filename || "Импорт скрипта");
    setIngested(false);
    setChunks(0);
    setSummary(null);
    setSources([]);
    setScript(doc.script || null);
    setAudioJobId(null);
    setBatchJobId(null);
    setError(null);
    setNotice(null);
    setAutoIngesting(false);
    setDocumentListRefresh((r) => r + 1);
    setShowDocumentListWhenActive(false);
    setViewerCitation(null);
    setViewerError(null);
    setSummaryExpanded(false);
    setScriptExpanded(!!doc.script);
  }

  const handleViewerError = useCallback((message) => {
    const msg = normalizeUiErrorMessage(message);
    if (!msg) {
      setViewerError(null);
      return;
    }
    setViewerError(msg);
  }, []);

  function handleFocusSourcePanel() {
    if (!showStudio) {
      setShowStudio(true);
      setTimeout(() => scrollToRef(studioSourceRef), 0);
      return;
    }
    scrollToRef(studioSourceRef);
  }

  async function handleUploadedDocument(id, name, uploadMeta = null) {
    const documentIdNext = String(id || "").trim();
    if (!documentIdNext) return;
    const meta = uploadMeta && typeof uploadMeta === "object" ? uploadMeta : {};
    if (meta.duplicate) {
      try {
        const existing = await getDocument(documentIdNext);
        handleOpenDocument(existing || {
          document_id: documentIdNext,
          filename: String(meta.filename || name || "").trim(),
          ingested: !!meta.existing_ingested,
          chunks: 0,
          summary: null,
          sources: [],
          script: null,
        });
        setNotice(
          String(
            meta.message
            || `Дубликат по хешу: открыт уже существующий документ ${String(meta.filename || name || documentIdNext).trim()}.`,
          ),
        );
      } catch (e) {
        handleUiError(e?.message || "Не удалось открыть существующий документ-дубликат.");
      }
      return;
    }
    const seq = autoIngestSeqRef.current + 1;
    autoIngestSeqRef.current = seq;

    setDocumentId(documentIdNext);
    setFilename(String(name || "").trim());
    setIngested(false);
    setChunks(0);
    setSummary(null);
    setSources([]);
    setScript(null);
    setAudioJobId(null);
    setBatchJobId(null);
    setError(null);
    setNotice(null);
    setAutoIngesting(true);
    setViewerCitation(null);
    setViewerError(null);
    setShowDocumentListWhenActive(false);
    setDocumentListRefresh((r) => r + 1);

    try {
      const res = await ingest(documentIdNext);
      if (autoIngestSeqRef.current !== seq) return;
      setIngested(true);
      setChunks(Number(res?.chunks || 0));
      setDocumentListRefresh((r) => r + 1);
    } catch (e) {
      if (autoIngestSeqRef.current !== seq) return;
      handleUiError(e?.message || "Не удалось выполнить автоиндексацию документа.");
    } finally {
      if (autoIngestSeqRef.current === seq) {
        setAutoIngesting(false);
      }
    }
  }

  useEffect(() => {
    setViewerError(null);
  }, [documentId, viewerCitation]);

  async function handlePinChatQa(payload) {
    const projectId = String(activeProjectContext?.project_id || "").trim();
    if (!projectId) {
      setError("Сначала выберите активный набор документов, чтобы закрепить Q&A.");
      throw new Error("Набор документов не выбран");
    }
    try {
      await addProjectPin(projectId, payload || {});
      setProjectNotebookRefresh((v) => v + 1);
    } catch (e) {
      handleUiError(e?.message || "Не удалось закрепить Q&A в notebook.");
      throw e;
    }
  }

  const helpTabs = [
    { id: "start", label: "Быстрый старт" },
    { id: "chat", label: "Чат и источники" },
    { id: "summary", label: "Саммари и скрипт" },
    { id: "voice", label: "Голос и экспорт" },
    { id: "settings", label: "Настройки" },
    { id: "troubleshoot", label: "Диагностика" },
  ];

  function renderHelpTabContent() {
    if (helpTab === "start") {
      return (
        <>
          <p className="help-lead">
            Исследовательский ассистент — локальная система анализа документов: загрузка, автоиндексация,
            чат с опорой на источники, саммари, скрипт подкаста и озвучка.
          </p>
          <h4>Что делает программа</h4>
          <ul className="help-list">
            <li>Загружает документы и URL, извлекает текст и индексирует фрагменты для RAG-поиска.</li>
            <li>Дает ответы с цитированием и переходом к исходному месту в документе.</li>
            <li>Генерирует саммари, скрипт подкаста и итоговое аудио.</li>
            <li>Работает в упрощенном и полном интерфейсе без потери контекста.</li>
          </ul>
          <h4>Пошаговый сценарий</h4>
          <ol className="help-list">
            <li>Добавьте файл (PDF, DOCX, DOC, RTF, ODT, OTD, PPT, PPTX, DJVU, TXT) или ссылку в панели файлов.</li>
            <li>Дождитесь автоиндексации: статус документа должен перейти в готовое состояние.</li>
            <li>Задайте вопрос в чате (Q&amp;A, Conv RAG или Сравнение).</li>
            <li>Кликните по citation-чипу, чтобы открыть источник и подсветку.</li>
            <li>При необходимости создайте саммари, скрипт и запустите озвучивание.</li>
          </ol>
        </>
      );
    }

    if (helpTab === "chat") {
      return (
        <>
          <h4>Режимы чата</h4>
          <ul className="help-list">
            <li><strong>Q&amp;A:</strong> точечный ответ по выбранным документам.</li>
            <li><strong>Conv RAG:</strong> учитывает историю диалога и подходит для длительной сессии.</li>
            <li><strong>Сравнение:</strong> сопоставляет несколько документов по вашему фокусу.</li>
            <li><strong>Строго по источникам:</strong> ограничивает ответ только найденными фрагментами.</li>
          </ul>
          <h4>Навигация чат → источник</h4>
          <ul className="help-list">
            <li>Под каждым ответом есть citation-чипы в формате `document/chunk` со score.</li>
            <li>Клик по чипу автоматически открывает источник в viewer без дополнительных кнопок.</li>
            <li>Для длинных текстов используется оконный режим с автоскроллом к подсветке.</li>
            <li>Для PDF доступны переключатели «Документ/Текст» и переход по якорям.</li>
          </ul>
          <h4>Метрики ответа</h4>
          <ul className="help-list">
            <li><strong>Надежность:</strong> агрегированная оценка качества ответа.</li>
            <li><strong>Поиск:</strong> насколько релевантные фрагменты найдены.</li>
            <li><strong>Покрытие:</strong> насколько ответ подтвержден цитатами.</li>
            <li><strong>Опора:</strong> насколько формулировки ответа привязаны к источнику.</li>
          </ul>
        </>
      );
    }

    if (helpTab === "summary") {
      return (
        <>
          <h4>Саммари</h4>
          <ul className="help-list">
            <li>Режимы отображения: Markdown/Текст, копирование итогового результата.</li>
            <li>Понятные состояния: до генерации, во время генерации, после завершения.</li>
          </ul>
          <h4>Скрипт подкаста</h4>
          <ul className="help-list">
            <li>Фильтры реплик: голос, поиск и только проблемные строки.</li>
            <li>Операции по строке: lock, регенерация, статусы loading/done/error.</li>
            <li>На длинных сценариях скролл изолирован и не ломает общий layout.</li>
          </ul>
          <h4>Рекомендуемый порядок</h4>
          <ol className="help-list">
            <li>Сначала саммари, чтобы получить устойчивую основу.</li>
            <li>Потом скрипт с точечной правкой сложных мест.</li>
            <li>После проверки реплик запускайте озвучку и экспорт.</li>
          </ol>
        </>
      );
    }

    if (helpTab === "voice") {
      return (
        <>
          <h4>Голосовой режим</h4>
          <ul className="help-list">
            <li>Push-to-talk: нажмите «Начать запись», задайте вопрос, остановите запись.</li>
            <li>Поддерживается распознавание речи, генерация ответа и TTS-озвучивание.</li>
            <li>Источники последнего voice-ответа доступны отдельным списком.</li>
          </ul>
          <h4>Экспорт и артефакты</h4>
          <ul className="help-list">
            <li>Скрипт экспортируется в JSON/TXT/SRT/DOCX, пакетно — ZIP.</li>
            <li>Аудио формируется в MP3, прогресс отображается в Job-панели.</li>
          </ul>
        </>
      );
    }

    if (helpTab === "settings") {
      return (
        <>
          <h4>LM Studio</h4>
          <ul className="help-list">
            <li>Настраиваются URL сервера, модель, температура и лимиты.</li>
            <li>Если ответы нестабильны, уменьшайте температуру и сужайте вопрос.</li>
          </ul>
          <h4>Профили генерации</h4>
          <ul className="help-list">
            <li><strong>Быстро:</strong> минимальная задержка.</li>
            <li><strong>Баланс:</strong> дефолтный режим.</li>
            <li><strong>Глубоко:</strong> более детальный анализ.</li>
          </ul>
          <h4>TTS и постобработка</h4>
          <ul className="help-list">
            <li>Голоса назначаются по ролям с тестовым прослушиванием.</li>
            <li>Доступны настройки intro/background/outro и словарь произношений.</li>
          </ul>
        </>
      );
    }

    return (
      <>
        <h4>Частые проблемы</h4>
        <ul className="help-list">
          <li><strong>Источник не открывается:</strong> проверьте индексацию и активный документ.</li>
          <li><strong>Фрагмент не подсвечен:</strong> переключите «Текст/Документ» и нажмите citation повторно.</li>
          <li><strong>Ответ общий:</strong> включите «Строго по источникам» и уточните формулировку.</li>
          <li><strong>LLM недоступна:</strong> проверьте LM Studio URL/модель в настройках.</li>
        </ul>
        <h4>Данные и локальность</h4>
        <ul className="help-list">
          <li>Документы, индексы и история хранятся локально в рабочей директории.</li>
          <li>Очистка базы удаляет локальные документы, индексы и историю чата.</li>
        </ul>
        <h4>Статусы</h4>
        <ul className="help-list">
          <li>Topbar показывает состояние документа, саммари и скрипта.</li>
          <li>Ошибки показываются баннером в верхней части интерфейса.</li>
          <li>Ход генерации аудио и batch-задач отображается в Job-панели.</li>
        </ul>
      </>
    );
  }

  return (
    <div className={`app${liteMode ? " lite-screen" : ""}`} style={{ "--topbar-offset": `${topbarOffset}px` }}>
      <header className="topbar" ref={topbarRef}>
        <div className="topbar-brand">
          <span className="logo" aria-hidden="true">ON</span>
          <div className="topbar-brand-copy">
            <h1>Исследовательский ассистент</h1>
          </div>
        </div>
        <div className="topbar-statuses">
          <button
            type="button"
            className={`topbar-pill topbar-pill-button ${documentStatus.tone}`.trim()}
            onClick={() => scrollToRef(chatRef)}
            title="Перейти к чату"
          >
            {topbarDocumentStatus}
          </button>
          <button
            type="button"
            className={`topbar-pill topbar-pill-button ${summaryStatusItem.tone}`.trim()}
            onClick={() => scrollToRef(summaryRef)}
            title="Перейти к саммари"
          >
            {topbarSummaryStatus}
          </button>
          <button
            type="button"
            className={`topbar-pill topbar-pill-button ${scriptStatusItem.tone}`.trim()}
            onClick={() => scrollToRef(scriptRef)}
            title="Перейти к скрипту"
          >
            {topbarScriptStatus}
          </button>
        </div>
        <div className="topbar-actions">
          <button
            type="button"
            className={liteMode ? "" : "secondary"}
            onClick={() => setLiteMode((v) => !v)}
            title={liteMode ? "Вернуться в полный интерфейс" : "Перейти в упрощенный интерфейс"}
          >
            {liteMode ? "Полный режим" : "Упрощенный режим"}
          </button>
          <button
            type="button"
            className="secondary"
            onClick={() => setTheme((t) => (t === "night" ? "day" : "night"))}
            title="Переключить тему оформления"
          >
            {theme === "night" ? "Тема: ночь" : "Тема: день"}
          </button>
          <button
            type="button"
            className="secondary"
            onClick={() => {
              setHelpTab("start");
              setShowHelpModal(true);
            }}
            title="Описание программы и справка"
          >
            О программе
          </button>
          {!liteMode && (
            <button
              type="button"
              className="secondary"
              onClick={() => scrollToRef(actionRef)}
              title="Перейти к панели генерации"
            >
              К генерации
            </button>
          )}
          {!liteMode && (
            <>
              <button
                type="button"
                className="secondary"
                onClick={() => setShowSources((v) => !v)}
                title={showSources ? "Скрыть панель документов" : "Показать панель документов"}
              >
                {showSources ? "Скрыть документы" : "Показать документы"}
              </button>
              <button
                type="button"
                className="secondary"
                onClick={() => setShowStudio((v) => !v)}
                title={showStudio ? "Скрыть панель просмотра документа" : "Показать панель просмотра документа"}
              >
                {showStudio ? "Скрыть просмотр" : "Показать просмотр"}
              </button>
              <button
                className="secondary"
                onClick={() => {
                  if (!showStudio) setShowStudio(true);
                  setShowSettings(!showSettings);
                }}
                title="Настройки модели, голосов и постобработки"
              >
                {showSettings ? "Скрыть настройки" : "Настройки"}
              </button>
            </>
          )}
        </div>
      </header>

      {hasVisibleError && (
        <div className="error-banner">
          <span>{error}</span>
          <button className="secondary" onClick={() => setError(null)} title="Закрыть сообщение об ошибке">Закрыть</button>
        </div>
      )}

      {!!notice && (
        <div className="notice-banner">
          <span>{notice}</span>
          <button className="secondary" onClick={() => setNotice(null)} title="Закрыть уведомление">Закрыть</button>
        </div>
      )}

      {liteMode ? (
        <div className="lite-layout">
          <main className="lite-chat-column">
            <section className="section-shell lite-upload-shell">
              <div className="section-head">
                <h2>Файл</h2>
                <span className="section-subhead">{filename || "Добавьте источник"}</span>
              </div>
              <UploadPanel
                compact
                onUploaded={(id, name, meta) => {
                  void handleUploadedDocument(id, name, meta);
                }}
                onError={handleUiError}
                onScriptOnlyImported={handleScriptOnlyImported}
              />
            </section>

            <section className="section-shell lite-chat-shell">
              <div className="section-head">
                <h2>Чат</h2>
                <span className="section-subhead">Минимальный интерфейс</span>
              </div>
              <ChatPanel
                currentDocumentId={documentId}
                externalSelectedDocumentIds={sourceSelectedDocumentIds}
                activeProjectContext={activeProjectContext}
                onPinQa={handlePinChatQa}
                onError={handleUiError}
                liteMode
                onSourceCitationOpen={handleOpenSourceCitation}
              />
            </section>
          </main>

          <aside className="lite-source-column">
            <section className="section-shell lite-source-shell">
              <div className="section-head">
                <h2>Просмотр документа</h2>
                <span className={`section-subhead ${viewerSubheadToneClass}`.trim()}>
                  {viewerSubheadLite}
                </span>
              </div>
              <SourceViewerPane
                citation={viewerCitation}
                documentId={documentId}
                filename={filename}
                onError={handleViewerError}
                onLoadingChange={setViewerLoading}
              />
            </section>
          </aside>
        </div>
      ) : (
        <div className={`app-layout${showStudio ? "" : " no-studio"}${showSources ? "" : " sources-collapsed"}`}>
          <aside className={`sources-column${showSources ? "" : " collapsed"}`}>
            <section className="section-shell">
              <div className="section-head">
                <h2>Документы</h2>
                {showSources ? (
                  <span className={`section-subhead ${autoIngesting ? "state-loading" : ""}`.trim()}>
                    {documentId
                      ? (ingested ? "Активный документ" : (autoIngesting ? "Автоиндексация…" : "Документ загружен, без индекса"))
                      : "Загрузка документов"}
                  </span>
                ) : (
                  <button
                    type="button"
                    className="secondary compact-expand-btn"
                    onClick={() => setShowSources(true)}
                    title="Показать панель документов"
                  >
                    Документы
                  </button>
                )}
              </div>
              <UploadPanel
                documentId={documentId}
                filename={filename}
                compact={!showSources}
                showDocumentListWhenActive={showDocumentListWhenActive}
                onUploaded={(id, name, meta) => { void handleUploadedDocument(id, name, meta); }}
                onError={handleUiError}
                onReset={handleReset}
                onDeleteDocument={documentId ? handleDeleteCurrentDocument : null}
                onBackToList={documentId ? () => setShowDocumentListWhenActive((v) => !v) : null}
                onScriptOnlyImported={handleScriptOnlyImported}
              />
              {showSources && (!documentId || showDocumentListWhenActive) && (
                <DocumentList
                  documentListRefresh={documentListRefresh}
                  projectNotebookRefresh={projectNotebookRefresh}
                  onOpen={handleOpenDocument}
                  onError={handleUiError}
                  onBatchJob={setBatchJobId}
                  onSelectionChange={setSourceSelectedDocumentIds}
                  onProjectContextChange={setActiveProjectContext}
                  participants={participants}
                  batchScriptSettings={{ ...podcastScriptSettings, role_llm_map: roleLlmMap }}
                />
              )}
            </section>
          </aside>

          <main className="center-column">
            <section className="section-shell" ref={chatRef}>
              <div className="section-head">
                <h2>Чат</h2>
                <span className="section-subhead">Q&A и сравнение источников</span>
              </div>
              {!documentId && (
                <div className="workspace-empty-hint">
                  Добавьте или откройте документ в панели «Документы», чтобы начать Q&A и переходы по источникам.
                </div>
              )}
              <ChatPanel
                currentDocumentId={documentId}
                externalSelectedDocumentIds={sourceSelectedDocumentIds}
                activeProjectContext={activeProjectContext}
                onPinQa={handlePinChatQa}
                onError={handleUiError}
                onSourceCitationOpen={handleOpenSourceCitation}
              />
            </section>

            {documentId && (
              <div ref={actionRef}>
                <ActionBar
                  documentId={documentId}
                  ingested={ingested}
                  chunks={chunks}
                  hasScript={!!script}
                  participants={participants}
                  roleLlmMap={roleLlmMap}
                  onScenarioRolesChange={handleScenarioRolesChange}
                  onIngested={(n) => { setIngested(true); setChunks(n); }}
                  onSummary={(s, src) => { setSummary(s); setSources(src); setStreamingSummary(null); }}
                  onStreamingSummary={setStreamingSummary}
                  onScript={(s) => { setScript(s); setStreamingScript(null); }}
                  onStreamingScript={setStreamingScript}
                  onAudioJob={setAudioJobId}
                  onScriptSettingsChange={setPodcastScriptSettings}
                  projectDefaults={activeProjectContext?.settings || null}
                  onError={handleUiError}
                />
              </div>
            )}

            {documentId && (
              <div ref={summaryRef}>
                <details className="section-shell collapsible-panel" open={summaryExpanded} onToggle={(e) => setSummaryExpanded(e.currentTarget.open)}>
                  <summary>Саммари {streamingSummary ? "• генерация…" : ""}</summary>
                  <SummaryPanel
                    summary={streamingSummary ?? summary}
                    sources={streamingSummary ? [] : sources}
                    isStreaming={!!streamingSummary}
                  />
                </details>
              </div>
            )}
            {documentId && (
              <div ref={scriptRef}>
                <details className="section-shell collapsible-panel" open={scriptExpanded} onToggle={(e) => setScriptExpanded(e.currentTarget.open)}>
                  <summary>Скрипт {streamingScript ? "• генерация…" : ""}</summary>
                  <ScriptPanel
                    script={script}
                    documentId={documentId}
                    streamingRaw={streamingScript}
                    onScriptImported={setScript}
                    onError={handleUiError}
                  />
                </details>
              </div>
            )}
          </main>

          {showStudio && <aside className="studio-column">
            <section className="section-shell studio-source-shell" ref={studioSourceRef}>
              <div className="section-head">
                <h2>Просмотр документа</h2>
                <span className={`section-subhead ${viewerSubheadToneClass}`.trim()}>
                  {viewerSubheadFull}
                </span>
              </div>
              <SourceViewerPane
                citation={viewerCitation}
                documentId={documentId}
                filename={filename}
                onError={handleViewerError}
                onLoadingChange={setViewerLoading}
              />
            </section>

            <div ref={settingsRef}>
              {showSettings ? (
                <SettingsPanel
                  onClose={() => setShowSettings(false)}
                  participants={participants}
                  onParticipantsChange={(next) => setParticipants(normalizeParticipants(next))}
                  roleLlmMap={roleLlmMap}
                  onRoleLlmMapChange={setRoleLlmMap}
                />
              ) : null}
            </div>

            {documentId && <JobPanel jobId={audioJobId} label="Генерация аудио" documentId={documentId} artifactKind="audio" />}
            {batchJobId && <JobPanel jobId={batchJobId} label="Пакетная генерация" />}
          </aside>}
        </div>
      )}

      {showHelpModal && (
        <div className="modal-overlay" onClick={() => setShowHelpModal(false)}>
          <div className="modal-card help-modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <h3>О программе и справка</h3>
              <button type="button" className="secondary" onClick={() => setShowHelpModal(false)}>Закрыть</button>
            </div>
            <div className="help-tabs" role="tablist" aria-label="Разделы справки">
              {helpTabs.map((tab) => {
                const isActive = helpTab === tab.id;
                return (
                  <button
                    key={tab.id}
                    type="button"
                    className={`help-tab-btn ${isActive ? "is-active" : ""}`.trim()}
                    role="tab"
                    aria-selected={isActive}
                    onClick={() => setHelpTab(tab.id)}
                  >
                    {tab.label}
                  </button>
                );
              })}
            </div>
            <section className="help-tab-panel" role="tabpanel">
              {renderHelpTabContent()}
            </section>

            <p className="help-author">Автор Рыльцин И.А.</p>
          </div>
        </div>
      )}
    </div>
  );
}

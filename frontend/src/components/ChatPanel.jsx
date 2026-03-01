import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  listDocuments,
  compareDocuments,
  queryChat,
  consumeChatStream,
  consumeConversationalChatStream,
  getChatHistory,
  clearChatHistory,
  consumeVoiceQaStream,
  downloadUrl,
} from "../api/client";
import { buildDocumentStatus } from "../utils/uiStatus";
import "./ChatPanel.css";
import SourceViewerModal from "./SourceViewerModal";

const VOICE_WAKE_WORD_KEY = "voice-assistant-wake-word";
const VOICE_WAKE_BARGE_IN_KEY = "voice-assistant-wake-barge-in";
const VOICE_STT_MODEL_KEY = "voice-assistant-stt-model";
const VOICE_SETTINGS_EVENT = "voice-assistant-settings-changed";
const TEXT_CHAT_SCOPE_KEY = "chat-text-context-scope";
const ADVANCED_CONTROLS_KEY = "chat-advanced-controls-open";
const USE_SUMMARY_CONTEXT_KEY = "chat-use-summary-context";
const CHAT_KNOWLEDGE_MODE_KEY = "chat-knowledge-mode";
const ANSWER_MODE_OPTIONS = [
  { value: "default", label: "Баланс", title: "Сбалансированный ответ" },
  { value: "quote", label: "С цитатами", title: "Максимально опираться на цитаты и фрагменты" },
  { value: "overview", label: "Обзор", title: "Структурный обзор по материалу" },
  { value: "formulas", label: "Формулы", title: "Акцент на формулы, графики и числовые детали" },
];
const TEXT_SCOPE_OPTIONS = [
  { value: "auto", label: "Авто", title: "Автоматически: набор документов при 2+ документах, иначе активный документ" },
  { value: "single", label: "Один документ", title: "Всегда использовать только активный документ" },
  { value: "collection", label: "Набор документов", title: "Использовать выбранный набор документов (2+ документа)" },
];
const ANSWER_LENGTH_OPTIONS = [
  { value: "short", label: "Короткий" },
  { value: "medium", label: "Средний" },
  { value: "long", label: "Длинный" },
];
const KNOWLEDGE_MODE_OPTIONS = [
  { value: "document_only", label: "Только документ" },
  { value: "hybrid_model", label: "Документ + знания модели" },
];

function ToolbarIcon({ name }) {
  if (name === "trash") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M9 3h6l1 2h5v2H3V5h5l1-2Zm-3 6h12l-1 11a2 2 0 0 1-2 2H9a2 2 0 0 1-2-2L6 9Zm4 2v8h2v-8h-2Zm4 0v8h2v-8h-2Z" />
      </svg>
    );
  }
  if (name === "settings") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="m19.4 13 .2-1-.2-1 2-1.6-2-3.4-2.4.7a7 7 0 0 0-1.7-1L14.9 3h-3.8l-.4 2.7a7 7 0 0 0-1.7 1l-2.4-.7-2 3.4 2 1.6-.2 1 .2 1-2 1.6 2 3.4 2.4-.7a7 7 0 0 0 1.7 1l.4 2.7h3.8l.4-2.7a7 7 0 0 0 1.7-1l2.4.7 2-3.4-2-1.6ZM12 15.2A3.2 3.2 0 1 1 12 8.8a3.2 3.2 0 0 1 0 6.4Z" />
      </svg>
    );
  }
  if (name === "stop") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M6 6h12v12H6z" />
      </svg>
    );
  }
  if (name === "retry") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M12 5a7 7 0 1 1-6.7 9h2.2A5 5 0 1 0 9 8.4L11 10H5V4l2.3 2.3A7 7 0 0 1 12 5Z" />
      </svg>
    );
  }
  if (name === "assistant") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M12 3a4 4 0 0 1 4 4v5a4 4 0 0 1-8 0V7a4 4 0 0 1 4-4Zm-6 9h2a4 4 0 0 0 8 0h2a6 6 0 0 1-5 5.9V21h-2v-3.1A6 6 0 0 1 6 12Z" />
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

function normalizeVoiceSttModel(raw) {
  const v = String(raw || "").trim().toLowerCase();
  return ["tiny", "base", "small"].includes(v) ? v : "small";
}

function normalizeQuestionMode(raw) {
  const v = String(raw || "").trim().toLowerCase();
  return ["default", "quote", "overview", "formulas"].includes(v) ? v : "default";
}

function normalizeTextChatScope(raw) {
  const v = String(raw || "").trim().toLowerCase();
  return ["auto", "single", "collection"].includes(v) ? v : "auto";
}

function normalizeAnswerLength(raw) {
  const v = String(raw || "").trim().toLowerCase();
  return ["short", "medium", "long"].includes(v) ? v : "medium";
}

function normalizeKnowledgeMode(raw) {
  const v = String(raw || "").trim().toLowerCase();
  return v === "hybrid_model" ? "hybrid_model" : "document_only";
}

const EXTERNAL_KNOWLEDGE_MARKER_RE = /(^|\n)\s*(?:Вне документа|Гипотеза модели|Предложение модели|Критика модели)\s*:/i;
const EXTERNAL_KNOWLEDGE_LINE_RE = /^\s*(?:Вне документа|Гипотеза модели|Предложение модели|Критика модели)\s*:/i;

function hasExternalKnowledgeMarker(text) {
  return EXTERNAL_KNOWLEDGE_MARKER_RE.test(String(text || ""));
}

function isExternalKnowledgeLine(text) {
  return EXTERNAL_KNOWLEDGE_LINE_RE.test(String(text || ""));
}

function compactSectionPath(raw) {
  const src = String(raw || "").trim();
  if (!src) return "";
  const parts = src.split(/\s*(?:\/|>|→|\|)\s*/).map((x) => x.trim()).filter(Boolean);
  const tail = parts.length ? parts[parts.length - 1] : src;
  return tail.length > 26 ? `${tail.slice(0, 23)}...` : tail;
}

function formatCitationChipLabel(citation) {
  const c = citation || {};
  const chunkId = typeof c.chunk_index === "number" && c.chunk_index >= 0 ? c.chunk_index : c.chunk_id;
  const base = `${c.document_id}/#${chunkId}`;
  const extras = [];
  const sourceType = String(c.source_type || "").trim().toLowerCase();
  if (sourceType.includes("ocr")) extras.push("OCR");
  if (sourceType.includes("table")) extras.push("таблица");
  else if (sourceType.includes("figure")) extras.push("рисунок");
  if (c.page !== undefined && c.page !== null && String(c.page).trim() !== "") extras.push(`стр. ${c.page}`);
  if (c.section_path) extras.push(compactSectionPath(c.section_path));
  return extras.length ? `${base} · ${extras.join(" · ")}` : base;
}

function normalizeConfidenceBreakdown(raw) {
  const src = raw && typeof raw === "object" ? raw : {};
  const toUnit = (value) => {
    const n = Number(value);
    if (!Number.isFinite(n)) return null;
    return Math.max(0, Math.min(1, n));
  };
  const retrievalQuality = toUnit(src.retrieval_quality);
  const evidenceCoverage = toUnit(src.evidence_coverage);
  const answerGrounding = toUnit(src.answer_grounding);
  if (retrievalQuality === null && evidenceCoverage === null && answerGrounding === null) return null;
  return {
    retrieval_quality: retrievalQuality ?? 0,
    evidence_coverage: evidenceCoverage ?? 0,
    answer_grounding: answerGrounding ?? 0,
  };
}

const CONFIDENCE_TOOLTIPS = {
  reliability: "Надежность: агрегированная оценка качества ответа",
  retrieval: "Поиск: насколько релевантные фрагменты документа найдены для ответа",
  coverage: "Покрытие: насколько ответ подтверждён цитатами из документа",
  grounding: "Опора: насколько формулировки ответа опираются на источник, а не на общие знания модели",
};

function formatConfidenceBreakdownInline(raw) {
  const b = normalizeConfidenceBreakdown(raw);
  if (!b) return "";
  const rq = Math.round(b.retrieval_quality * 100);
  const ec = Math.round(b.evidence_coverage * 100);
  const ag = Math.round(b.answer_grounding * 100);
  return `Поиск: ${rq}% · Покрытие: ${ec}% · Опора: ${ag}%`;
}

function buildConfidenceMeta(prefix, confidence, breakdown) {
  const parts = [];
  const p = String(prefix || "").trim();
  if (p) parts.push(p);
  if (typeof confidence === "number" && Number.isFinite(confidence)) {
    parts.push(`Надежность: ${Math.round(confidence * 100)}%`);
  }
  const breakdownText = formatConfidenceBreakdownInline(breakdown);
  if (breakdownText) parts.push(breakdownText);
  return parts.join(" · ").trim();
}

function citationStableKey(citation) {
  const c = citation || {};
  const evidenceId = String(c.evidence_id || "").trim();
  if (evidenceId) return `ev:${evidenceId}`;
  const anchorId = String(c.anchor_id || "").trim();
  if (anchorId) return `a:${anchorId}`;
  return `${String(c.document_id || "")}/${String(c.chunk_id || "")}:${String(c.chunk_index ?? "na")}`;
}

function dedupeCitations(citations, maxItems = 8) {
  const rows = Array.isArray(citations) ? citations : [];
  const out = [];
  const seen = new Set();
  for (const row of rows) {
    if (!row || typeof row !== "object") continue;
    const key = citationStableKey(row);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(row);
    if (out.length >= maxItems) break;
  }
  return out;
}

function escapeHtml(s) {
  return (s || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function safeHref(raw) {
  const href = (raw || "").trim();
  if (/^https?:\/\//i.test(href) || /^mailto:/i.test(href)) return href;
  return "";
}

function renderInline(text) {
  let t = text;
  t = t.replace(/`([^`]+)`/g, "<code>$1</code>");
  t = t.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  t = t.replace(/\*([^*]+)\*/g, "<em>$1</em>");
  t = t.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_, label, href) => {
    const safe = safeHref(href);
    if (!safe) return label;
    return `<a href="${safe}" target="_blank" rel="noopener noreferrer">${label}</a>`;
  });
  return t;
}

function markdownToHtml(markdown) {
  const src = escapeHtml(markdown || "").replace(/\r\n/g, "\n");
  const lines = src.split("\n");
  const out = [];
  let inUl = false;
  let inOl = false;

  function closeLists() {
    if (inUl) out.push("</ul>");
    if (inOl) out.push("</ol>");
    inUl = false;
    inOl = false;
  }

  for (const lineRaw of lines) {
    const line = lineRaw.trimEnd();
    if (!line.trim()) {
      closeLists();
      continue;
    }
    const h = line.match(/^(#{1,6})\s+(.+)$/);
    if (h) {
      closeLists();
      const lvl = h[1].length;
      out.push(`<h${lvl}>${renderInline(h[2])}</h${lvl}>`);
      continue;
    }
    const ul = line.match(/^\s*[-*]\s+(.+)$/);
    if (ul) {
      if (inOl) {
        out.push("</ol>");
        inOl = false;
      }
      if (!inUl) {
        out.push("<ul>");
        inUl = true;
      }
      const itemClass = isExternalKnowledgeLine(ul[1]) ? ' class="chat-external-knowledge-block"' : '';
      out.push(`<li${itemClass}>${renderInline(ul[1])}</li>`);
      continue;
    }
    const ol = line.match(/^\s*\d+\.\s+(.+)$/);
    if (ol) {
      if (inUl) {
        out.push("</ul>");
        inUl = false;
      }
      if (!inOl) {
        out.push("<ol>");
        inOl = true;
      }
      const itemClass = isExternalKnowledgeLine(ol[1]) ? ' class="chat-external-knowledge-block"' : '';
      out.push(`<li${itemClass}>${renderInline(ol[1])}</li>`);
      continue;
    }
    closeLists();
    const paragraphClass = isExternalKnowledgeLine(line) ? ' class="chat-external-knowledge-block"' : '';
    out.push(`<p${paragraphClass}>${renderInline(line)}</p>`);
  }
  closeLists();
  return out.join("\n");
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

  // Handle unfinished streamed block: "<think> ... " without closing tag.
  const openIdx = answer.toLowerCase().lastIndexOf("<think>");
  if (openIdx >= 0) {
    chunks.push(answer.slice(openIdx + "<think>".length).trim());
    answer = answer.slice(0, openIdx);
  }

  answer = answer.replace(/<\/think>/gi, "").trim();
  const reasoning = chunks.filter(Boolean).join("\n\n").trim();
  return { answer, reasoning };
}

function isRawSourceTailLine(rawLine) {
  const line = String(rawLine || "").trim();
  if (!line) return false;
  if (/^источники\s*:?\s*$/i.test(line)) return true;
  if (/^(?:[-*]\s*)?\[[^\]]*(?:doc\/chunk|chunk=|doc=)[^\]]*\]\s*$/i.test(line)) return true;
  if (/^(?:[-*]\s*)?(?:doc\/chunk|chunk=|doc=)[\w\-./#:=\s]+$/i.test(line)) return true;
  return false;
}

function sanitizeAssistantAnswer(rawAnswer, citations = []) {
  const hasCitations = Array.isArray(citations) && citations.length > 0;
  const original = String(rawAnswer || "").replace(/\r\n/g, "\n").trim();
  if (!original) return "";

  let text = original;
  const markerMatches = [...text.matchAll(/\n\s*Источники\s*:/gi)];
  if (markerMatches.length > 0) {
    const markerIdx = markerMatches[markerMatches.length - 1].index;
    const tail = text.slice(markerIdx);
    if (hasCitations || /doc\/chunk|chunk=|doc=/i.test(tail)) {
      text = text.slice(0, markerIdx).trim();
    }
  }

  const lines = text.split("\n");
  let removedSourceTail = false;
  while (lines.length > 0) {
    const last = String(lines[lines.length - 1] || "").trim();
    if (!last) {
      lines.pop();
      continue;
    }
    if (isRawSourceTailLine(last)) {
      lines.pop();
      removedSourceTail = true;
      continue;
    }
    break;
  }

  const cleaned = lines.join("\n").trim();
  if (cleaned) return cleaned;
  if (removedSourceTail || hasCitations) return "";
  return original;
}

function formatVoiceQaError(err) {
  const stage = String(err?.stage || "").toLowerCase();
  const stageLabel = {
    stt: "Распознавание речи",
    rag: "Поиск по документу",
    llm: "Генерация ответа",
    tts: "Озвучка ответа",
    unknown: "Voice Q&A",
  }[stage] || "Voice Q&A";
  const message = String(err?.message || "Ошибка голосового Q&A").trim();
  const hint = String(err?.hint || "").trim();
  const retryable = typeof err?.retryable === "boolean" ? err.retryable : null;
  let out = `${stageLabel}: ${message}`;
  if (hint) out += ` Подсказка: ${hint}`;
  if (retryable === true) out += " Можно повторить попытку.";
  return out;
}

export default function ChatPanel({
  currentDocumentId,
  externalSelectedDocumentIds = [],
  activeProjectContext = null,
  onPinQa,
  onError,
  liteMode = false,
  onSourceCitationOpen,
}) {
  const threadId = "main-chat";
  const [docs, setDocs] = useState([]);
  const [selected, setSelected] = useState({});
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState("qa"); // qa | conv | compare
  const [messages, setMessages] = useState([]);
  const [activeCitation, setActiveCitation] = useState(null);
  const [sourceViewerCitation, setSourceViewerCitation] = useState(null);
  const [docFilter, setDocFilter] = useState("");
  const [strictSources, setStrictSources] = useState(() => {
    try {
      const raw = localStorage.getItem("chat-strict-sources");
      if (raw === null) return false;
      return raw === "1";
    } catch (_) {
      return false;
    }
  });
  const [useSummaryContext, setUseSummaryContext] = useState(() => {
    try {
      const raw = localStorage.getItem(USE_SUMMARY_CONTEXT_KEY);
      if (raw === null) return false;
      return raw === "1";
    } catch (_) {
      return false;
    }
  });
  const [questionMode, setQuestionMode] = useState(() => {
    try {
      return normalizeQuestionMode(localStorage.getItem("chat-question-mode") || "default");
    } catch (_) {
      return "default";
    }
  });
  const [answerLength, setAnswerLength] = useState(() => {
    try {
      return normalizeAnswerLength(localStorage.getItem("chat-answer-length") || "medium");
    } catch (_) {
      return "medium";
    }
  });
  const [knowledgeMode, setKnowledgeMode] = useState(() => {
    try {
      return normalizeKnowledgeMode(localStorage.getItem(CHAT_KNOWLEDGE_MODE_KEY) || "document_only");
    } catch (_) {
      return "document_only";
    }
  });
  const [showAdvancedControls, setShowAdvancedControls] = useState(() => {
    try {
      const raw = localStorage.getItem(ADVANCED_CONTROLS_KEY);
      if (raw === null) return false;
      return raw === "1";
    } catch (_) {
      return false;
    }
  });
  const [textChatScope, setTextChatScope] = useState(() => {
    try {
      return normalizeTextChatScope(localStorage.getItem(TEXT_CHAT_SCOPE_KEY) || "auto");
    } catch (_) {
      return "auto";
    }
  });
  const [voiceStage, setVoiceStage] = useState("idle"); // idle | listening | transcribing | thinking | speaking
  const [voiceSupported, setVoiceSupported] = useState(true);
  const [voiceRecording, setVoiceRecording] = useState(false);
  const [voicePlaying, setVoicePlaying] = useState(false);
  const [voiceRetryInfo, setVoiceRetryInfo] = useState(null);
  const [lastVoiceResult, setLastVoiceResult] = useState(null);
  const [activeVoiceSource, setActiveVoiceSource] = useState(null);
  const [voiceWakeWord, setVoiceWakeWord] = useState(() => {
    try {
      return (localStorage.getItem(VOICE_WAKE_WORD_KEY) || "Гена").trim() || "Гена";
    } catch (_) {
      return "Гена";
    }
  });
  const [voiceSttModel, setVoiceSttModel] = useState(() => {
    try {
      return normalizeVoiceSttModel(localStorage.getItem(VOICE_STT_MODEL_KEY) || "small");
    } catch (_) {
      return "small";
    }
  });
  const [voiceWakeArmed, setVoiceWakeArmed] = useState(false);
  const [voiceWakeSupported, setVoiceWakeSupported] = useState(false);
  const [voiceWakeStatus, setVoiceWakeStatus] = useState("off");
  const [voiceSessionMode, setVoiceSessionMode] = useState(null);
  const [voiceWakeBargeInEnabled, setVoiceWakeBargeInEnabled] = useState(() => {
    try {
      const raw = localStorage.getItem(VOICE_WAKE_BARGE_IN_KEY);
      if (raw === null) return false;
      return raw === "1";
    } catch (_) {
      return false;
    }
  });
  const compareDefaultSelectionKeyRef = useRef("");
  const projectSettingsKeyRef = useRef("");
  const [voiceVisualQuestion, setVoiceVisualQuestion] = useState("");
  const [voiceVisualAnswer, setVoiceVisualAnswer] = useState("");
  const [voiceVisualLevel, setVoiceVisualLevel] = useState(0);
  const [voiceModalHidden, setVoiceModalHidden] = useState(false);
  const [pinningMessages, setPinningMessages] = useState({});
  const [pinnedMessages, setPinnedMessages] = useState({});
  const [voiceVadEnabled, setVoiceVadEnabled] = useState(() => {
    try {
      const raw = localStorage.getItem("chat-voice-vad");
      if (raw === null) return true;
      return raw === "1";
    } catch (_) {
      return true;
    }
  });
  const streamEpochRef = useRef(0);
  const busyRef = useRef(false);
  const voiceRecordingRef = useRef(false);
  const voicePlayingRef = useRef(false);
  const voiceStageRef = useRef("idle");
  const mediaRecorderRef = useRef(null);
  const mediaStreamRef = useRef(null);
  const mediaChunksRef = useRef([]);
  const voiceAudioRef = useRef(null);
  const voiceAudioQueueRef = useRef([]);
  const voiceAudioQueueRunningRef = useRef(false);
  const voiceAudioEpochRef = useRef(0);
  const voiceTtsStreamingRef = useRef(false);
  const voiceTtsSegmentCountRef = useRef(0);
  const voicePlaybackSuppressedRef = useRef(false);
  const voicePhaseTimerRef = useRef(null);
  const lastVoiceRequestRef = useRef(null);
  const voiceRequestAbortRef = useRef(null);
  const vadAudioContextRef = useRef(null);
  const vadAnalyserRef = useRef(null);
  const vadSourceRef = useRef(null);
  const vadRafRef = useRef(null);
  const vadStateRef = useRef({ speechSeen: false, silenceSince: 0, startedAt: 0 });
  const voiceMeterUpdateTsRef = useRef(0);
  const wakeRecognitionRef = useRef(null);
  const wakeRestartTimerRef = useRef(null);
  const wakeCooldownRef = useRef(0);
  const wakeManualStopRef = useRef(false);
  const wakeAudioContextRef = useRef(null);
  const wakeAudioSourceRef = useRef(null);
  const wakeAudioProcessorRef = useRef(null);
  const wakeMediaStreamRef = useRef(null);
  const autoSourceMessageKeyRef = useRef("");

  function isNotFoundError(err) {
    const t = String(err?.message || err || "").toLowerCase();
    return t.includes("not found") || t.includes("\"detail\":\"not found\"");
  }

  useEffect(() => {
    busyRef.current = busy;
  }, [busy]);

  useEffect(() => {
    voiceRecordingRef.current = voiceRecording;
  }, [voiceRecording]);

  useEffect(() => {
    voicePlayingRef.current = voicePlaying;
  }, [voicePlaying]);

  useEffect(() => {
    voiceStageRef.current = voiceStage;
  }, [voiceStage]);

  useEffect(() => {
    let cancelled = false;
    Promise.all([listDocuments(), getChatHistory(threadId, 80)])
      .then(([rows, history]) => {
        if (cancelled) return;
        setDocs(rows || []);
        const msgs = (history && history.messages) || [];
        if (Array.isArray(msgs)) setMessages(msgs);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [currentDocumentId]);

  useEffect(() => {
    if (!currentDocumentId) return;
    setSelected((prev) => ({ ...prev, [currentDocumentId]: true }));
  }, [currentDocumentId]);

  useEffect(() => {
    if (!Array.isArray(externalSelectedDocumentIds) || externalSelectedDocumentIds.length === 0) return;
    setSelected((prev) => {
      const next = { ...prev };
      for (const id of externalSelectedDocumentIds) {
        next[id] = true;
      }
      return next;
    });
  }, [externalSelectedDocumentIds]);

  const externalCompareSelectedIds = useMemo(() => {
    const out = [];
    const seen = new Set();
    const raw = Array.isArray(externalSelectedDocumentIds) ? externalSelectedDocumentIds : [];
    for (const id of raw) {
      const key = String(id || "").trim();
      if (!key || seen.has(key)) continue;
      seen.add(key);
      out.push(key);
    }
    return out;
  }, [externalSelectedDocumentIds]);

  const activeProjectDocIds = useMemo(() => {
    const raw = Array.isArray(activeProjectContext?.document_ids) ? activeProjectContext.document_ids : [];
    const out = [];
    const seen = new Set();
    for (const id of raw) {
      const key = String(id || "").trim();
      if (!key || seen.has(key)) continue;
      seen.add(key);
      out.push(key);
    }
    return out;
  }, [activeProjectContext]);

  useEffect(() => {
    const projectId = String(activeProjectContext?.project_id || "").trim();
    const settings = activeProjectContext?.settings;
    if (!projectId || !settings || typeof settings !== "object") {
      projectSettingsKeyRef.current = "";
      return;
    }
    const chat = settings.chat && typeof settings.chat === "object" ? settings.chat : {};
    const key = `${projectId}:${String(activeProjectContext?.updated_at || "")}:${JSON.stringify(chat)}`;
    if (projectSettingsKeyRef.current === key) return;
    projectSettingsKeyRef.current = key;
    const nextStrict = typeof chat.strict_sources === "boolean" ? chat.strict_sources : false;
    const nextSummaryContext = typeof chat.use_summary_context === "boolean" ? chat.use_summary_context : false;
    setStrictSources(nextStrict);
    setUseSummaryContext(nextSummaryContext);
    setQuestionMode(normalizeQuestionMode(chat.question_mode));
    setAnswerLength(normalizeAnswerLength(chat.answer_length));
    setTextChatScope(normalizeTextChatScope(chat.scope));
  }, [activeProjectContext]);

  function applyExactCompareSelection(ids) {
    const wanted = new Set((Array.isArray(ids) ? ids : []).map((id) => String(id || "").trim()).filter(Boolean));
    setSelected((prev) => {
      const next = { ...prev };
      for (const d of docs) {
        const docId = String(d?.document_id || "").trim();
        if (!docId) continue;
        next[docId] = wanted.has(docId);
      }
      return next;
    });
  }

  useEffect(() => {
    try {
      localStorage.setItem("chat-strict-sources", strictSources ? "1" : "0");
    } catch (_) {}
  }, [strictSources]);

  useEffect(() => {
    try {
      localStorage.setItem(USE_SUMMARY_CONTEXT_KEY, useSummaryContext ? "1" : "0");
    } catch (_) {}
  }, [useSummaryContext]);

  useEffect(() => {
    try {
      localStorage.setItem("chat-question-mode", normalizeQuestionMode(questionMode));
    } catch (_) {}
  }, [questionMode]);

  useEffect(() => {
    try {
      localStorage.setItem("chat-answer-length", normalizeAnswerLength(answerLength));
    } catch (_) {}
  }, [answerLength]);

  useEffect(() => {
    try {
      localStorage.setItem(CHAT_KNOWLEDGE_MODE_KEY, normalizeKnowledgeMode(knowledgeMode));
    } catch (_) {}
  }, [knowledgeMode]);

  useEffect(() => {
    try {
      localStorage.setItem(ADVANCED_CONTROLS_KEY, showAdvancedControls ? "1" : "0");
    } catch (_) {}
  }, [showAdvancedControls]);

  useEffect(() => {
    try {
      localStorage.setItem(TEXT_CHAT_SCOPE_KEY, normalizeTextChatScope(textChatScope));
    } catch (_) {}
  }, [textChatScope]);

  useEffect(() => {
    try {
      localStorage.setItem("chat-voice-vad", voiceVadEnabled ? "1" : "0");
    } catch (_) {}
  }, [voiceVadEnabled]);

  useEffect(() => {
    try {
      localStorage.setItem(VOICE_WAKE_BARGE_IN_KEY, voiceWakeBargeInEnabled ? "1" : "0");
    } catch (_) {}
  }, [voiceWakeBargeInEnabled]);

  useEffect(() => {
    const syncWakeWord = () => {
      try {
        setVoiceWakeWord((localStorage.getItem(VOICE_WAKE_WORD_KEY) || "Гена").trim() || "Гена");
        setVoiceSttModel(normalizeVoiceSttModel(localStorage.getItem(VOICE_STT_MODEL_KEY) || "small"));
      } catch (_) {}
    };
    const onStorage = (event) => {
      if (!event || event.key === VOICE_WAKE_WORD_KEY || event.key === VOICE_STT_MODEL_KEY) syncWakeWord();
    };
    const onCustom = () => syncWakeWord();
    window.addEventListener("storage", onStorage);
    window.addEventListener(VOICE_SETTINGS_EVENT, onCustom);
    return () => {
      window.removeEventListener("storage", onStorage);
      window.removeEventListener(VOICE_SETTINGS_EVENT, onCustom);
    };
  }, []);

  useEffect(() => {
    if (!voiceWakeArmed) {
      stopWakeRecognition(true);
      return;
    }
    if (!voiceWakeSupported) {
      setVoiceWakeStatus("error");
      return;
    }
    if (
      voiceRecording ||
      (!voiceWakeBargeInEnabled && (busy || voicePlaying || voiceStage === "speaking"))
    ) {
      stopWakeRecognition(false);
      return;
    }
    startWakeRecognition();
    return () => {
      if (!voiceWakeArmed) return;
      stopWakeRecognition(false);
    };
  }, [voiceWakeArmed, voiceWakeSupported, voiceWakeWord, voiceWakeBargeInEnabled, voiceRecording, busy, voicePlaying, voiceStage]);

  useEffect(() => {
    if (liteMode && mode === "compare") {
      setMode("qa");
    }
  }, [liteMode, mode]);

  const selectedIds = useMemo(() => {
    const ids = docs.filter((d) => selected[d.document_id]).map((d) => d.document_id);
    const ext = Array.isArray(externalSelectedDocumentIds) ? externalSelectedDocumentIds : [];
    for (const id of ext) {
      if (!ids.some((x) => String(x) === String(id))) ids.push(id);
    }
    if (currentDocumentId) {
      const hasCurrent = ids.some((id) => String(id) === String(currentDocumentId));
      const shouldIncludeCurrent = selected[currentDocumentId] || ids.length === 0;
      if (!hasCurrent && shouldIncludeCurrent) {
        ids.push(currentDocumentId);
      }
    }
    return ids;
  }, [docs, selected, currentDocumentId, externalSelectedDocumentIds]);

  const compareDocs = useMemo(() => {
    const q = (docFilter || "").trim().toLowerCase();
    if (!q) return docs;
    return docs.filter((d) => {
      const name = String(d.filename || "").toLowerCase();
      const id = String(d.document_id || "").toLowerCase();
      return name.includes(q) || id.includes(q);
    });
  }, [docs, docFilter]);

  useEffect(() => {
    if (mode !== "compare") {
      compareDefaultSelectionKeyRef.current = "";
      return;
    }
    if (!docs.length) return;
    if (externalCompareSelectedIds.length < 2) return;
    const sourceKey = [
      String(activeProjectContext?.project_id || "source-selection"),
      activeProjectContext?.is_dirty ? "dirty" : "clean",
      externalCompareSelectedIds.join("|"),
    ].join("::");
    if (compareDefaultSelectionKeyRef.current === sourceKey) return;
    applyExactCompareSelection(externalCompareSelectedIds);
    compareDefaultSelectionKeyRef.current = sourceKey;
  }, [mode, docs, externalCompareSelectedIds, activeProjectContext]);

  const singleDocId = useMemo(() => {
    if (currentDocumentId) return currentDocumentId;
    const ext = Array.isArray(externalSelectedDocumentIds) ? externalSelectedDocumentIds : [];
    if (ext.length > 0) return ext[0];
    if (selectedIds.length > 0) return selectedIds[0];
    return "";
  }, [currentDocumentId, externalSelectedDocumentIds, selectedIds]);
  const singleDocRecord = useMemo(
    () => docs.find((d) => String(d.document_id || "") === String(singleDocId || "")) || null,
    [docs, singleDocId],
  );
  const singleDocStatus = useMemo(
    () =>
      buildDocumentStatus({
        documentId: singleDocId,
        filename: singleDocRecord?.filename || "",
        ingested: !!singleDocRecord?.ingested,
        chunks: Number(singleDocRecord?.chunks || 0),
        includeLabel: false,
      }),
    [singleDocId, singleDocRecord],
  );

  const collectionChatTargetIds = useMemo(() => {
    if (externalCompareSelectedIds.length >= 2) return externalCompareSelectedIds;
    if (activeProjectDocIds.length >= 2 && !activeProjectContext?.is_dirty) return activeProjectDocIds;
    return [];
  }, [externalCompareSelectedIds, activeProjectDocIds, activeProjectContext]);

  const textChatTargetIds = useMemo(() => {
    if (mode === "compare") return selectedIds;
    if (textChatScope === "single") return singleDocId ? [singleDocId] : [];
    if (textChatScope === "collection") return collectionChatTargetIds;
    if (collectionChatTargetIds.length >= 2) return collectionChatTargetIds;
    return singleDocId ? [singleDocId] : [];
  }, [mode, selectedIds, textChatScope, collectionChatTargetIds, singleDocId]);

  const textChatCollectionAvailable = collectionChatTargetIds.length >= 2;

  const textChatContextLabel = useMemo(() => {
    if (mode === "compare") return "";
    if (textChatScope === "collection" && !textChatCollectionAvailable) {
      return "Набор документов: выберите минимум 2 документа";
    }
    if (textChatTargetIds.length >= 2) {
      const projectName = String(activeProjectContext?.name || "").trim();
      if (projectName) return `Набор документов: ${projectName} · ${textChatTargetIds.length} док.`;
      return `Набор документов · ${textChatTargetIds.length} док.`;
    }
    const currentName = docs.find((d) => d.document_id === singleDocId)?.filename || "не выбран";
    return `Текущий документ: ${currentName}`;
  }, [mode, textChatScope, textChatCollectionAvailable, textChatTargetIds, activeProjectContext, docs, singleDocId]);

  const voiceChatTargetIds = useMemo(() => {
    if (mode === "compare") return [];
    return textChatTargetIds;
  }, [mode, textChatTargetIds]);

  const voicePrimaryDocId = useMemo(() => {
    if (voiceChatTargetIds.length > 0) return String(voiceChatTargetIds[0] || "");
    return singleDocId ? String(singleDocId) : "";
  }, [voiceChatTargetIds, singleDocId]);

  useEffect(() => {
    setLastVoiceResult((prev) => {
      if (!prev) return null;
      if (!singleDocId) return null;
      return String(prev.document_id) === String(singleDocId) ? prev : null;
    });
    setActiveVoiceSource(null);
  }, [singleDocId]);

  useEffect(() => {
    const hasMediaRecorder =
      typeof window !== "undefined" &&
      !!window.MediaRecorder &&
      !!window.navigator?.mediaDevices?.getUserMedia;
    setVoiceSupported(!!hasMediaRecorder);
    const hasWakeAudio =
      typeof window !== "undefined" &&
      !!window.WebSocket &&
      !!window.navigator?.mediaDevices?.getUserMedia &&
      !!(window.AudioContext || window.webkitAudioContext);
    setVoiceWakeSupported(!!hasWakeAudio);
  }, []);

  useEffect(() => {
    return () => {
      stopWakeRecognition(true);
      stopVadMonitoring();
      if (voicePhaseTimerRef.current) {
        clearTimeout(voicePhaseTimerRef.current);
        voicePhaseTimerRef.current = null;
      }
      try {
        voiceAudioRef.current?.pause();
      } catch (_) {}
      voiceAudioRef.current = null;
      try {
        mediaRecorderRef.current?.stop?.();
      } catch (_) {}
      mediaRecorderRef.current = null;
      const stream = mediaStreamRef.current;
      if (stream) {
        for (const track of stream.getTracks?.() || []) {
          try { track.stop(); } catch (_) {}
        }
      }
      mediaStreamRef.current = null;
    };
  }, []);

  function clearVoicePhaseTimer() {
    if (voicePhaseTimerRef.current) {
      clearTimeout(voicePhaseTimerRef.current);
      voicePhaseTimerRef.current = null;
    }
  }

  function abortActiveVoiceRequest() {
    try {
      voiceRequestAbortRef.current?.abort?.();
    } catch (_) {}
  }

  function normalizedWakeWord(raw) {
    return String(raw || "").trim().toLowerCase().replace(/ё/g, "е");
  }

  function stopWakeRecognition(manual = false) {
    if (wakeRestartTimerRef.current) {
      clearTimeout(wakeRestartTimerRef.current);
      wakeRestartTimerRef.current = null;
    }
    if (manual) wakeManualStopRef.current = true;
    const ws = wakeRecognitionRef.current;
    wakeRecognitionRef.current = null;
    if (ws) {
      try {
        ws.onopen = null;
        ws.onmessage = null;
        ws.onerror = null;
        ws.onclose = null;
        if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
          ws.close(1000, "wake_stop");
        }
      } catch (_) {}
    }
    try { wakeAudioProcessorRef.current?.disconnect?.(); } catch (_) {}
    try { wakeAudioSourceRef.current?.disconnect?.(); } catch (_) {}
    wakeAudioProcessorRef.current = null;
    wakeAudioSourceRef.current = null;
    const wakeCtx = wakeAudioContextRef.current;
    wakeAudioContextRef.current = null;
    if (wakeCtx && typeof wakeCtx.close === "function") {
      try { void wakeCtx.close(); } catch (_) {}
    }
    const wakeStream = wakeMediaStreamRef.current;
    if (wakeStream) {
      for (const track of wakeStream.getTracks?.() || []) {
        try { track.stop(); } catch (_) {}
      }
    }
    wakeMediaStreamRef.current = null;
    if (manual) setVoiceWakeStatus("off");
  }

  async function triggerWakeCapture() {
    const now = Date.now();
    if (now - (wakeCooldownRef.current || 0) < 1800) return;
    wakeCooldownRef.current = now;
    setVoiceWakeStatus("heard");
    setVoiceModalHidden(false);
    setVoiceVisualQuestion("");
    setVoiceVisualAnswer("");
    if (voiceRecordingRef.current) return;
    if (busyRef.current && (voiceStageRef.current === "transcribing" || voiceStageRef.current === "thinking")) {
      abortActiveVoiceRequest();
      const started = Date.now();
      while (busyRef.current && Date.now() - started < 4000) {
        // eslint-disable-next-line no-await-in-loop
        await new Promise((r) => setTimeout(r, 60));
      }
      if (busyRef.current) {
        onError?.("Не удалось прервать текущий voice-запрос после wake-word.");
        return;
      }
    }
    if (voicePlayingRef.current || voiceStageRef.current === "speaking") {
      stopVoicePlayback();
    }
    await startVoiceRecording();
  }

  function startWakeRecognition() {
    if (!voiceWakeArmed || !voiceWakeSupported) return;
    if (
      voiceRecordingRef.current ||
      (!voiceWakeBargeInEnabled && (busyRef.current || voicePlayingRef.current || voiceStageRef.current === "speaking"))
    ) return;
    if (wakeRecognitionRef.current) return;
    if (typeof window === "undefined" || !window.WebSocket) return;
    wakeManualStopRef.current = false;
    const wsProto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${wsProto}//${window.location.host}/api/voice_wake/ws?wake_word=${encodeURIComponent(voiceWakeWord)}`;

    function resampleToPcm16(input, inRate, outRate = 16000) {
      const src = input instanceof Float32Array ? input : new Float32Array(input || []);
      if (!src.length) return null;
      if (!Number.isFinite(inRate) || inRate <= 0) return null;
      if (inRate === outRate) {
        const out = new Int16Array(src.length);
        for (let i = 0; i < src.length; i += 1) {
          const s = Math.max(-1, Math.min(1, src[i]));
          out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
        }
        return out.buffer;
      }
      const ratio = inRate / outRate;
      const outLen = Math.max(1, Math.floor(src.length / ratio));
      const out = new Int16Array(outLen);
      let offsetResult = 0;
      let offsetBuffer = 0;
      while (offsetResult < outLen) {
        const nextOffsetBuffer = Math.min(src.length, Math.round((offsetResult + 1) * ratio));
        let sum = 0;
        let count = 0;
        for (let i = offsetBuffer; i < nextOffsetBuffer; i += 1) {
          sum += src[i];
          count += 1;
        }
        const sample = count ? (sum / count) : 0;
        const s = Math.max(-1, Math.min(1, sample));
        out[offsetResult] = s < 0 ? s * 0x8000 : s * 0x7fff;
        offsetResult += 1;
        offsetBuffer = nextOffsetBuffer;
      }
      return out.buffer;
    }

    (async () => {
      try {
        const stream = await window.navigator.mediaDevices.getUserMedia({
          audio: {
            channelCount: 1,
            noiseSuppression: true,
            echoCancellation: true,
            autoGainControl: true,
          },
        });
        if (wakeManualStopRef.current || !voiceWakeArmed) {
          for (const t of stream.getTracks?.() || []) {
            try { t.stop(); } catch (_) {}
          }
          return;
        }

        const ws = new WebSocket(wsUrl);
        ws.binaryType = "arraybuffer";
        wakeRecognitionRef.current = ws;
        wakeMediaStreamRef.current = stream;
        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        const ctx = new AudioCtx();
        const srcNode = ctx.createMediaStreamSource(stream);
        const proc = ctx.createScriptProcessor(4096, 1, 1);
        wakeAudioContextRef.current = ctx;
        wakeAudioSourceRef.current = srcNode;
        wakeAudioProcessorRef.current = proc;
        srcNode.connect(proc);
        proc.connect(ctx.destination);

        proc.onaudioprocess = (event) => {
          const socket = wakeRecognitionRef.current;
          if (!socket || socket.readyState !== WebSocket.OPEN) return;
          const ch = event.inputBuffer?.getChannelData?.(0);
          if (!ch || !ch.length) return;
          const buf = resampleToPcm16(ch, ctx.sampleRate || 48000, 16000);
          if (!buf) return;
          try {
            socket.send(buf);
          } catch (_) {}
        };

        ws.onopen = () => {
          setVoiceWakeStatus("listening");
        };
        ws.onmessage = (ev) => {
          try {
            const msg = JSON.parse(String(ev.data || ""));
            if (!msg || typeof msg !== "object") return;
            if (msg.type === "wake_detected") {
              void triggerWakeCapture();
              return;
            }
            if (msg.type === "error") {
              setVoiceWakeStatus("error");
            }
          } catch (_) {}
        };
        ws.onerror = () => {
          setVoiceWakeStatus("error");
        };
        ws.onclose = () => {
          const same = wakeRecognitionRef.current === ws;
          if (same) wakeRecognitionRef.current = null;
          try { proc.disconnect(); } catch (_) {}
          try { srcNode.disconnect(); } catch (_) {}
          if (wakeAudioProcessorRef.current === proc) wakeAudioProcessorRef.current = null;
          if (wakeAudioSourceRef.current === srcNode) wakeAudioSourceRef.current = null;
          const closeCtx = wakeAudioContextRef.current;
          if (closeCtx === ctx) wakeAudioContextRef.current = null;
          try { void ctx.close(); } catch (_) {}
          const closeStream = wakeMediaStreamRef.current;
          if (closeStream === stream) {
            for (const t of stream.getTracks?.() || []) {
              try { t.stop(); } catch (_) {}
            }
            wakeMediaStreamRef.current = null;
          }
          if (wakeManualStopRef.current) return;
          if (!voiceWakeArmed) return;
        if (
          voiceRecordingRef.current ||
          (!voiceWakeBargeInEnabled && (busyRef.current || voicePlayingRef.current || voiceStageRef.current === "speaking"))
        ) return;
          wakeRestartTimerRef.current = setTimeout(() => {
            wakeRestartTimerRef.current = null;
            startWakeRecognition();
          }, 450);
        };
      } catch (e) {
        wakeRecognitionRef.current = null;
        setVoiceWakeStatus("error");
        onError?.(e?.message || "Не удалось запустить локальный wake-word");
      }
    })();
  }

  function resolveVoiceTargetMode() {
    if (voiceWakeArmed && (voiceSessionMode === "qa" || voiceSessionMode === "conv")) {
      return voiceSessionMode;
    }
    return mode === "conv" ? "conv" : "qa";
  }

  function openSourceCitation(citation) {
    if (!citation || typeof citation !== "object") return;
    const nextKey = citationStableKey(citation);
    if (!onSourceCitationOpen && sourceViewerCitation) {
      const currentKey = citationStableKey(sourceViewerCitation);
      if (currentKey && currentKey === nextKey) return;
    }
    if (typeof onSourceCitationOpen === "function") {
      onSourceCitationOpen(citation);
      return;
    }
    setSourceViewerCitation(citation);
  }

  function resolveMessageSourceCitation(messageIdx, message) {
    const own = dedupeCitations(message?.citations, 8);
    if (own.length > 0) return own[0];
    if (String(message?.role || "") !== "user") return null;
    for (let i = messageIdx + 1; i < messages.length; i += 1) {
      const next = messages[i];
      if (!next || next.role !== "assistant") continue;
      const nextCitations = dedupeCitations(next.citations, 8);
      if (nextCitations.length > 0) return nextCitations[0];
    }
    return null;
  }

  function handleMessageSourceJump(event, messageIdx, message) {
    const target = event?.target;
    if (target instanceof Element && target.closest("button, a, input, textarea, select, summary, label")) return;
    if (typeof window !== "undefined") {
      const selectedText = String(window.getSelection?.()?.toString?.() || "").trim();
      if (selectedText) return;
    }
    const citation = resolveMessageSourceCitation(messageIdx, message);
    if (!citation) return;
    openSourceCitation(citation);
  }

  useEffect(() => {
    if (!liteMode || typeof onSourceCitationOpen !== "function") return;
    const allowedDocIds = new Set([
      ...textChatTargetIds.map((id) => String(id || "").trim()),
      String(currentDocumentId || "").trim(),
    ].filter(Boolean));

    let matchedCitation = null;
    let fallbackCitation = null;
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      const m = messages[i];
      if (!m || m.role !== "assistant") continue;
      const citations = Array.isArray(m.citations) ? m.citations : [];
      if (!citations.length) continue;
      const c0 = citations[0];
      if (!fallbackCitation) fallbackCitation = c0;
      const cDoc = String(c0.document_id || "").trim();
      if (allowedDocIds.size > 0 && !allowedDocIds.has(cDoc)) continue;
      matchedCitation = c0;
      break;
    }

    const nextCitation = matchedCitation || null;
    if (nextCitation) {
      const key = citationStableKey(nextCitation);
      if (autoSourceMessageKeyRef.current === key) return;
      autoSourceMessageKeyRef.current = key;
      onSourceCitationOpen(nextCitation);
      return;
    }

    if (allowedDocIds.size > 0) {
      if (autoSourceMessageKeyRef.current === "__doc_only__") return;
      autoSourceMessageKeyRef.current = "__doc_only__";
      onSourceCitationOpen(null);
      return;
    }

    if (fallbackCitation) {
      const key = citationStableKey(fallbackCitation);
      if (autoSourceMessageKeyRef.current === key) return;
      autoSourceMessageKeyRef.current = key;
      onSourceCitationOpen(fallbackCitation);
    }
  }, [liteMode, messages, onSourceCitationOpen, textChatTargetIds, currentDocumentId]);

  function toggleVoiceAssistantWakeSession() {
    if (voiceWakeArmed) {
      setVoiceWakeArmed(false);
      setVoiceSessionMode(null);
      setVoiceWakeStatus("off");
      stopWakeRecognition(true);
      return;
    }
    if (mode === "compare") {
      onError?.("Wake-word ассистент доступен только в режимах Q&A и Conv RAG.");
      return;
    }
    if (!voicePrimaryDocId) {
      onError?.(
        textChatScope === "collection"
          ? "Для голосового режима по набору документов выберите минимум 2 документа в панели «Документы»."
          : "Нет документа для голосового режима. Откройте документ и повторите.",
      );
      return;
    }
    if (!voiceWakeSupported) {
      onError?.("Wake-word недоступен: нужен WebSocket + getUserMedia + AudioContext.");
      return;
    }
    setVoiceSessionMode(mode === "conv" ? "conv" : "qa");
    setVoiceWakeArmed(true);
    setVoiceWakeStatus("listening");
    setVoiceModalHidden(false);
    setVoiceVisualQuestion("");
    setVoiceVisualAnswer("");
  }

  function stopVadMonitoring() {
    if (vadRafRef.current) {
      try {
        cancelAnimationFrame(vadRafRef.current);
      } catch (_) {}
      vadRafRef.current = null;
    }
    try {
      vadSourceRef.current?.disconnect?.();
    } catch (_) {}
    vadSourceRef.current = null;
    try {
      vadAnalyserRef.current?.disconnect?.();
    } catch (_) {}
    vadAnalyserRef.current = null;
    const ctx = vadAudioContextRef.current;
    vadAudioContextRef.current = null;
    if (ctx && typeof ctx.close === "function") {
      try { void ctx.close(); } catch (_) {}
    }
    vadStateRef.current = { speechSeen: false, silenceSince: 0, startedAt: 0 };
    setVoiceVisualLevel(0);
    voiceMeterUpdateTsRef.current = 0;
  }

  async function pumpVoiceAudioQueue() {
    if (voiceAudioQueueRunningRef.current) return;
    const epoch = voiceAudioEpochRef.current;
    voiceAudioQueueRunningRef.current = true;
    try {
      while (voiceAudioEpochRef.current === epoch) {
        const next = voiceAudioQueueRef.current.shift();
        if (!next) break;
        const audio = new Audio(next.url);
        voiceAudioRef.current = audio;
        setVoicePlaying(true);
        setVoiceStage("speaking");
        // eslint-disable-next-line no-await-in-loop
        await new Promise((resolve) => {
          let settled = false;
          const finish = () => {
            if (settled) return;
            settled = true;
            if (voiceAudioRef.current === audio) voiceAudioRef.current = null;
            resolve();
          };
          audio.onended = finish;
          audio.onerror = finish;
          audio.onpause = () => {
            if (audio.ended) return;
            if (voiceAudioRef.current !== audio) finish();
          };
          audio.play().catch(() => finish());
        });
      }
    } finally {
      voiceAudioQueueRunningRef.current = false;
      if (voiceAudioEpochRef.current !== epoch) return;
      const queueEmpty = (voiceAudioQueueRef.current?.length || 0) === 0;
      if (queueEmpty) {
        setVoicePlaying(false);
        if (!voiceRecording && !voiceTtsStreamingRef.current) {
          setVoiceStage((prev) => (prev === "speaking" ? "idle" : prev));
        }
      }
    }
  }

  function enqueueVoicePlaybackFile(filename) {
    const name = String(filename || "").trim();
    if (!name || voicePlaybackSuppressedRef.current) return;
    voiceAudioQueueRef.current.push({
      filename: name,
      url: `${downloadUrl(name)}?v=${Date.now()}`,
    });
    void pumpVoiceAudioQueue();
  }

  function stopVoicePlayback(options = {}) {
    const suppressFuture = !!options?.suppressFuture;
    if (suppressFuture) {
      voicePlaybackSuppressedRef.current = true;
    }
    voiceAudioEpochRef.current += 1;
    voiceAudioQueueRef.current = [];
    voiceAudioQueueRunningRef.current = false;
    voiceTtsStreamingRef.current = false;
    voiceTtsSegmentCountRef.current = 0;
    try {
      voiceAudioRef.current?.pause();
    } catch (_) {}
    voiceAudioRef.current = null;
    setVoicePlaying(false);
    if (!voiceRecording) {
      setVoiceStage((prev) => (prev === "speaking" ? "idle" : prev));
    }
  }

  function releaseVoiceStream() {
    stopVadMonitoring();
    const stream = mediaStreamRef.current;
    if (!stream) return;
    for (const track of stream.getTracks?.() || []) {
      try { track.stop(); } catch (_) {}
    }
    mediaStreamRef.current = null;
  }

  function startVadMonitoring(stream) {
    if (!voiceVadEnabled) return;
    const Recorder = mediaRecorderRef.current;
    if (!stream || !Recorder) return;
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx) return;
    stopVadMonitoring();
    try {
      const ctx = new AudioCtx();
      const src = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 2048;
      analyser.smoothingTimeConstant = 0.85;
      src.connect(analyser);

      vadAudioContextRef.current = ctx;
      vadSourceRef.current = src;
      vadAnalyserRef.current = analyser;
      vadStateRef.current = { speechSeen: false, silenceSince: 0, startedAt: performance.now() };

      const data = new Uint8Array(analyser.fftSize);
      const SILENCE_MS_AFTER_SPEECH = 1400;
      const MAX_RECORDING_MS = 60000;
      const SPEECH_RMS_THRESHOLD = 0.02;

      const tick = () => {
        const recorder = mediaRecorderRef.current;
        if (!recorder || recorder.state !== "recording") {
          stopVadMonitoring();
          return;
        }
        const node = vadAnalyserRef.current;
        if (!node) {
          stopVadMonitoring();
          return;
        }
        node.getByteTimeDomainData(data);
        let sumSq = 0;
        for (let i = 0; i < data.length; i += 1) {
          const v = (data[i] - 128) / 128;
          sumSq += v * v;
        }
        const rms = Math.sqrt(sumSq / Math.max(1, data.length));
        const now = performance.now();
        const state = vadStateRef.current;
        if ((now - (voiceMeterUpdateTsRef.current || 0)) >= 70) {
          voiceMeterUpdateTsRef.current = now;
          const normalized = Math.max(0, Math.min(1, (rms - 0.006) / 0.05));
          setVoiceVisualLevel(normalized);
        }

        if (rms >= SPEECH_RMS_THRESHOLD) {
          state.speechSeen = true;
          state.silenceSince = 0;
        } else if (state.speechSeen) {
          if (!state.silenceSince) {
            state.silenceSince = now;
          } else if ((now - state.silenceSince) >= SILENCE_MS_AFTER_SPEECH) {
            stopVoiceRecording();
            return;
          }
        }

        if ((now - state.startedAt) >= MAX_RECORDING_MS) {
          stopVoiceRecording();
          return;
        }

        vadRafRef.current = requestAnimationFrame(tick);
      };

      vadRafRef.current = requestAnimationFrame(tick);
    } catch (_) {
      stopVadMonitoring();
    }
  }

  async function runVoiceQaRequest(payload) {
    const {
      blob,
      targetDocId,
      targetDocumentIds,
      targetMode,
      strictSourcesValue,
      useSummaryContextValue,
      questionModeValue,
      answerLengthValue,
      knowledgeModeValue,
      requestThreadId,
      historyLimitValue,
      sttModelValue,
    } = payload;
    clearVoicePhaseTimer();
    setVoiceStage("transcribing");
    setBusy(true);
    setVoiceRetryInfo(null);
    voicePlaybackSuppressedRef.current = false;
    voiceTtsStreamingRef.current = false;
    voiceTtsSegmentCountRef.current = 0;
    voiceAudioQueueRef.current = [];
    setVoiceVisualQuestion("");
    setVoiceVisualAnswer("");
    setVoiceModalHidden(false);
    const reqAbort = new AbortController();
    voiceRequestAbortRef.current = reqAbort;
    voicePhaseTimerRef.current = setTimeout(() => {
      setVoiceStage((prev) => (prev === "transcribing" ? "thinking" : prev));
      voicePhaseTimerRef.current = null;
    }, 1200);
    let userMessageId = null;
    let assistantMessageId = null;
    let streamDone = false;
    let streamError = null;
    let finalPayload = null;
    let partialAnswer = "";

    try {
      const mime = String(blob.type || "audio/webm").toLowerCase();
      const ext = mime.includes("ogg") ? "ogg" : mime.includes("mp4") ? "m4a" : "webm";
      await consumeVoiceQaStream(targetDocId, {
        audioBlob: blob,
        filename: `question.${ext}`,
        document_ids: Array.isArray(targetDocumentIds) ? targetDocumentIds : [targetDocId],
        strict_sources: strictSourcesValue,
        use_summary_context: useSummaryContextValue,
        question_mode: questionModeValue,
        answer_length: answerLengthValue,
        knowledge_mode: knowledgeModeValue,
        stt_model: sttModelValue,
        chat_mode: targetMode === "conv" ? "conv" : "qa",
        thread_id: requestThreadId,
        history_limit: historyLimitValue,
        with_tts: true,
        signal: reqAbort.signal,
      }, {
        onStatus: (evt) => {
          const status = String(evt?.status || "").toLowerCase();
          if (status === "stt_start") {
            setVoiceStage("transcribing");
            return;
          }
          if (status === "stt_partial") {
            clearVoicePhaseTimer();
            setVoiceStage("transcribing");
            const partialText = String(evt?.partial_text || "").trim();
            if (!partialText) return;
            setVoiceVisualQuestion(partialText);
            if (!userMessageId) {
              userMessageId = crypto.randomUUID();
              setMessages((prev) => [...prev, { id: userMessageId, role: "user", text: partialText }]);
              return;
            }
            setMessages((prev) =>
              prev.map((m) => (m.id === userMessageId ? { ...m, text: partialText } : m)),
            );
            return;
          }
          if (status === "stt_done") {
            clearVoicePhaseTimer();
            setVoiceStage("thinking");
            const qText = String(evt?.question_text || "").trim();
            if (!qText) return;
            setVoiceVisualQuestion(qText);
            if (!userMessageId) {
              userMessageId = crypto.randomUUID();
              setMessages((prev) => [...prev, { id: userMessageId, role: "user", text: qText }]);
              return;
            }
            setMessages((prev) =>
              prev.map((m) => (m.id === userMessageId ? { ...m, text: qText } : m)),
            );
            return;
          }
          if (status === "llm_start") {
            clearVoicePhaseTimer();
            setVoiceStage("thinking");
            setVoiceVisualAnswer("");
            if (!assistantMessageId) {
              assistantMessageId = crypto.randomUUID();
              setMessages((prev) => [...prev, { id: assistantMessageId, role: "assistant", text: "", meta: "Печатает…" }]);
            }
            return;
          }
          if (status === "llm_done") {
            setVoiceStage("thinking");
            return;
          }
          if (status === "tts_start") {
            voiceTtsStreamingRef.current = true;
            setVoiceStage((prev) => (prev === "speaking" ? "speaking" : "thinking"));
            return;
          }
          if (status === "tts_chunk_ready") {
            voiceTtsStreamingRef.current = true;
            const chunkFile = String(evt?.audio_filename || "").trim();
            if (chunkFile) {
              voiceTtsSegmentCountRef.current += 1;
              enqueueVoicePlaybackFile(chunkFile);
            }
            return;
          }
          if (status === "tts_done") {
            voiceTtsStreamingRef.current = false;
            const queueHasItems = (voiceAudioQueueRef.current?.length || 0) > 0;
            if (voicePlayingRef.current || queueHasItems) {
              setVoiceStage("speaking");
            } else {
              setVoiceStage((prev) => (prev === "speaking" ? "idle" : prev));
            }
          }
        },
        onChunk: (chunk) => {
          clearVoicePhaseTimer();
          setVoiceStage("thinking");
          partialAnswer += String(chunk || "");
          setVoiceVisualAnswer(partialAnswer);
          if (!assistantMessageId) {
            assistantMessageId = crypto.randomUUID();
            setMessages((prev) => [...prev, { id: assistantMessageId, role: "assistant", text: "", meta: "Печатает…" }]);
          }
          setMessages((prev) =>
            prev.map((m) => (m.id === assistantMessageId ? { ...m, text: partialAnswer } : m)),
          );
        },
        onDone: (payloadDone) => {
          streamDone = true;
          finalPayload = payloadDone || null;
        },
        onError: (e) => {
          streamError = e || new Error("Ошибка voice streaming");
        },
      });
      if (streamError) {
        throw streamError;
      }
      if (!streamDone || !finalPayload) {
        throw new Error("Voice Q&A stream завершился без результата");
      }
      const res = finalPayload;

      clearVoicePhaseTimer();
      setVoiceRetryInfo(null);
      setLastVoiceResult({
        document_id: targetDocId,
        document_ids: Array.isArray(targetDocumentIds) ? targetDocumentIds : [targetDocId],
        chat_mode: targetMode,
        question_text: res.question_text || "",
        answer_text: res.answer_text || "",
        confidence: res.confidence,
        confidence_breakdown: normalizeConfidenceBreakdown(res.confidence_breakdown),
        sources: Array.isArray(res.sources) ? res.sources : [],
        created_at: Date.now(),
      });
      setActiveVoiceSource(null);
      const finalAssistantText = String(res.answer_text || partialAnswer || "");
      setVoiceVisualQuestion(String(res.question_text || voiceVisualQuestion || ""));
      setVoiceVisualAnswer(finalAssistantText);
      const finalAssistantMeta = buildConfidenceMeta(
        targetMode === "conv" ? "Voice Conv RAG" : "Voice Q&A",
        res.confidence,
        res.confidence_breakdown,
      );
      setMessages((prev) => {
        let next = [...prev];
        if (!userMessageId) {
          userMessageId = crypto.randomUUID();
          next.push({ id: userMessageId, role: "user", text: res.question_text || "Голосовой вопрос" });
        } else {
          next = next.map((m) =>
            m.id === userMessageId
              ? { ...m, text: res.question_text || m.text || "Голосовой вопрос" }
              : m,
          );
        }
        if (!assistantMessageId) {
          assistantMessageId = crypto.randomUUID();
          next.push({
            id: assistantMessageId,
            role: "assistant",
            text: finalAssistantText,
            citations: res.sources || [],
            meta: finalAssistantMeta,
            has_model_knowledge_content: !!res.has_model_knowledge_content,
          });
          return next;
        }
        next = next.map((m) =>
          m.id === assistantMessageId
            ? {
                ...m,
                text: finalAssistantText,
                citations: res.sources || [],
                meta: finalAssistantMeta,
                has_model_knowledge_content: !!res.has_model_knowledge_content,
              }
            : m,
        );
        return next;
      });

      const usedStreamingTts = voiceTtsSegmentCountRef.current > 0;
      if (res.audio_filename && !usedStreamingTts && !voicePlaybackSuppressedRef.current) {
        stopVoicePlayback();
        const audio = new Audio(`${downloadUrl(res.audio_filename)}?v=${Date.now()}`);
        voiceAudioRef.current = audio;
        audio.onended = () => {
          setVoicePlaying(false);
          setVoiceStage("idle");
          if (voiceAudioRef.current === audio) voiceAudioRef.current = null;
        };
        audio.onerror = () => {
          setVoicePlaying(false);
          setVoiceStage("idle");
          if (voiceAudioRef.current === audio) voiceAudioRef.current = null;
          onError?.("Не удалось воспроизвести голосовой ответ");
        };
        setVoiceStage("speaking");
        setVoicePlaying(true);
        try {
          await audio.play();
        } catch (e) {
          setVoicePlaying(false);
          setVoiceStage("idle");
          onError?.(e?.message || "Браузер заблокировал автовоспроизведение ответа");
        }
      } else {
        const queueHasItems = (voiceAudioQueueRef.current?.length || 0) > 0;
        if (voicePlayingRef.current || queueHasItems || usedStreamingTts) {
          setVoiceStage((prev) => (prev === "idle" ? "speaking" : prev));
        } else {
          setVoiceStage("idle");
        }
      }
    } catch (e) {
      if (e?.code === "voice_qa_aborted") {
        clearVoicePhaseTimer();
        setVoiceStage("idle");
        setVoiceRetryInfo(null);
        setVoiceVisualLevel(0);
        if (userMessageId || assistantMessageId) {
          setMessages((prev) =>
            prev.filter((m) => m.id !== userMessageId && m.id !== assistantMessageId),
          );
        }
        return;
      }
      clearVoicePhaseTimer();
      setVoiceStage("idle");
      setVoiceVisualLevel(0);
      if (userMessageId || assistantMessageId) {
        setMessages((prev) =>
          prev.filter((m) => m.id !== userMessageId && m.id !== assistantMessageId),
        );
      }
      setVoiceRetryInfo(
        e?.retryable && lastVoiceRequestRef.current
          ? {
              retryable: true,
              stage: e.stage || "unknown",
              message: e.message || "Ошибка голосового Q&A",
            }
          : null,
      );
      onError?.(formatVoiceQaError(e));
    } finally {
      if (voiceRequestAbortRef.current === reqAbort) {
        voiceRequestAbortRef.current = null;
      }
      setBusy(false);
    }
  }

  async function handleVoiceRecorderStop(blob, targetDocId, targetDocumentIds) {
    const payload = {
      blob,
      targetDocId,
      targetDocumentIds: Array.isArray(targetDocumentIds) && targetDocumentIds.length > 0
        ? targetDocumentIds
        : [targetDocId],
      targetMode: resolveVoiceTargetMode(),
      strictSourcesValue: strictSources,
      useSummaryContextValue: useSummaryContext,
      questionModeValue: questionMode,
      answerLengthValue: answerLength,
      knowledgeModeValue: knowledgeMode,
      sttModelValue: voiceSttModel,
      requestThreadId: threadId,
      historyLimitValue: 12,
    };
    lastVoiceRequestRef.current = payload;
    await runVoiceQaRequest(payload);
  }

  async function startVoiceRecording() {
    if (busy || voiceRecording) return;
    if (mode === "compare" && !voiceWakeArmed) {
      onError?.("Голосовой режим пока недоступен в режиме сравнения.");
      return;
    }
    if (!voicePrimaryDocId) {
      onError?.(
        textChatScope === "collection"
          ? "Для голосового режима по набору документов выберите минимум 2 документа в панели «Документы»."
          : "Нет документа для голосового режима. Откройте документ и повторите.",
      );
      return;
    }
    if (!voiceSupported) {
      onError?.("В этом браузере недоступна запись с микрофона (MediaRecorder).");
      return;
    }

    stopVoicePlayback();
    stopWakeRecognition(false);
    clearVoicePhaseTimer();
    setVoiceModalHidden(false);
    setVoiceVisualQuestion("");
    setVoiceVisualAnswer("");
    setVoiceVisualLevel(0);

    try {
      const stream = await window.navigator.mediaDevices.getUserMedia({ audio: true });
      mediaStreamRef.current = stream;
      mediaChunksRef.current = [];

      let mimeType = "";
      const candidates = [
        "audio/webm;codecs=opus",
        "audio/webm",
        "audio/ogg;codecs=opus",
      ];
      if (window.MediaRecorder?.isTypeSupported) {
        mimeType = candidates.find((t) => window.MediaRecorder.isTypeSupported(t)) || "";
      }
      const recorder = mimeType ? new window.MediaRecorder(stream, { mimeType }) : new window.MediaRecorder(stream);
      mediaRecorderRef.current = recorder;

      recorder.ondataavailable = (event) => {
        if (event.data && event.data.size > 0) {
          mediaChunksRef.current.push(event.data);
        }
      };
      recorder.onerror = () => {
        setVoiceRecording(false);
        setVoiceStage("idle");
        releaseVoiceStream();
        onError?.("Ошибка записи с микрофона");
      };
      recorder.onstop = () => {
        setVoiceRecording(false);
        releaseVoiceStream();
        const chunks = mediaChunksRef.current || [];
        mediaChunksRef.current = [];
        mediaRecorderRef.current = null;
        const recordedMime = mimeType || recorder.mimeType || "audio/webm";
        const blob = new Blob(chunks, { type: recordedMime });
        if (!blob.size) {
          setVoiceStage("idle");
          onError?.("Запись пустая. Попробуйте ещё раз.");
          return;
        }
        void handleVoiceRecorderStop(blob, voicePrimaryDocId, voiceChatTargetIds);
      };

      recorder.start();
      startVadMonitoring(stream);
      setVoiceRecording(true);
      setVoiceStage("listening");
    } catch (e) {
      releaseVoiceStream();
      setVoiceRecording(false);
      setVoiceStage("idle");
      onError?.(e.message || "Не удалось получить доступ к микрофону");
    }
  }

  function stopVoiceRecording() {
    const recorder = mediaRecorderRef.current;
    if (!recorder) return;
    if (recorder.state === "inactive") return;
    try {
      recorder.stop();
      setVoiceStage("transcribing");
    } catch (e) {
      setVoiceRecording(false);
      setVoiceStage("idle");
      releaseVoiceStream();
      onError?.(e.message || "Не удалось остановить запись");
    }
  }

  async function toggleVoiceRecording() {
    if (voiceRecording) {
      stopVoiceRecording();
      return;
    }
    if (busy && (voiceStage === "transcribing" || voiceStage === "thinking")) {
      abortActiveVoiceRequest();
      const started = Date.now();
      while (busyRef.current && Date.now() - started < 4000) {
        // Wait for current request cleanup before opening mic.
        // eslint-disable-next-line no-await-in-loop
        await new Promise((r) => setTimeout(r, 60));
      }
      if (busyRef.current) {
        onError?.("Не удалось прервать текущий voice-запрос. Попробуйте ещё раз.");
        return;
      }
    }
    await startVoiceRecording();
  }

  async function retryLastVoiceRequest() {
    const payload = lastVoiceRequestRef.current;
    if (!payload || busy || voiceRecording) return;
    stopVoicePlayback();
    setVoiceModalHidden(false);
    await runVoiceQaRequest(payload);
  }

  async function handleSend() {
    const q = question.trim();
    if (!q) return;
    const targetIds = mode === "compare" ? selectedIds : textChatTargetIds;
    if (targetIds.length === 0) {
      onError?.(
        mode === "compare"
          ? "Выберите хотя бы один документ для чата."
          : textChatScope === "collection"
            ? "Для чата по набору документов выберите минимум 2 документа в панели «Документы»."
            : "Нет текущего документа. Откройте документ и повторите.",
      );
      return;
    }
    if (mode === "compare" && targetIds.length < 2) {
      onError?.("Для сравнения выберите минимум 2 документа.");
      return;
    }
    setBusy(true);
    const requestEpoch = streamEpochRef.current;
    setMessages((prev) =>
      prev.filter((m) => !(m.role === "assistant" && m.meta === "Печатает…" && !(m.text || "").trim())),
    );
    const userMessage = { id: crypto.randomUUID(), role: "user", text: q };
    setMessages((prev) => [...prev, userMessage]);
    setQuestion("");
    try {
      if (mode === "compare") {
        const res = await compareDocuments({ document_ids: targetIds, focus: q });
        setMessages((prev) => [...prev, { id: crypto.randomUUID(), role: "assistant", text: res.comparison, meta: `Сравнение: ${targetIds.length} документов` }]);
      } else if (mode === "qa") {
        const res = await queryChat({
          question: q,
          document_ids: targetIds,
          strict_sources: strictSources,
          use_summary_context: useSummaryContext,
          question_mode: questionMode,
          answer_length: answerLength,
          knowledge_mode: knowledgeMode,
        });
        if (streamEpochRef.current !== requestEpoch) return;
        const conf = buildConfidenceMeta("", res.confidence, res.confidence_breakdown);
        const scope = targetIds.length > 1 ? ` · ${targetIds.length} документов` : "";
        setMessages((prev) => [
          ...prev,
          {
            id: crypto.randomUUID(),
            role: "assistant",
            text: res.answer || "",
            citations: res.citations || [],
            meta: `${conf}${scope}`.trim(),
            has_model_knowledge_content: !!res.has_model_knowledge_content,
          },
        ]);
      } else {
        const assistantId = crypto.randomUUID();
        setMessages((prev) => [...prev, { id: assistantId, role: "assistant", text: "", meta: "Печатает…" }]);
        let convError = null;
        let done = false;
        await consumeConversationalChatStream(
          {
            question: q,
            document_ids: targetIds,
            thread_id: threadId,
            strict_sources: strictSources,
            use_summary_context: useSummaryContext,
            question_mode: questionMode,
            answer_length: answerLength,
            knowledge_mode: knowledgeMode,
          },
          {
            onChunk: (partial) => {
              if (streamEpochRef.current !== requestEpoch) return;
              setMessages((prev) =>
                prev.map((m) => (m.id === assistantId ? { ...m, text: partial } : m)),
              );
            },
            onDone: ({ answer, confidence, confidence_breakdown, citations, has_model_knowledge_content }) => {
              done = true;
              if (streamEpochRef.current !== requestEpoch) return;
              const conf = buildConfidenceMeta("Conversational RAG", confidence, confidence_breakdown);
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId
                    ? { ...m, text: answer || "", citations: citations || [], meta: conf, has_model_knowledge_content: !!has_model_knowledge_content }
                    : m,
                ),
              );
            },
            onError: (e) => {
              convError = e || new Error("Ошибка чата");
            },
          },
        );
        if (convError || !done) {
          const fallback = isNotFoundError(convError) || !done;
          if (!fallback) {
            throw convError;
          }
          const res = await queryChat({
            question: q,
            document_ids: targetIds,
            strict_sources: strictSources,
            use_summary_context: useSummaryContext,
            question_mode: questionMode,
            answer_length: answerLength,
            knowledge_mode: knowledgeMode,
          });
          if (streamEpochRef.current !== requestEpoch) return;
          setMode("qa");
          const conf = buildConfidenceMeta("Q&A fallback", res.confidence, res.confidence_breakdown);
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? { ...m, text: res.answer || "", citations: res.citations || [], meta: conf, has_model_knowledge_content: !!res.has_model_knowledge_content }
                : m,
            ),
          );
        }
      }
    } catch (e) {
      onError?.(e.message || "Ошибка чата");
      setMessages((prev) =>
        prev.filter((m) => !(m.role === "assistant" && m.meta === "Печатает…" && !(m.text || "").trim())),
      );
    } finally {
      if (streamEpochRef.current === requestEpoch) {
        setBusy(false);
      }
    }
  }

  const voiceStageLabel = {
    idle: "Готов",
    listening: "Слушаю",
    transcribing: "Распознаю",
    thinking: "Думаю",
    speaking: "Говорю",
  }[voiceStage] || "Готов";
  const canBargeIn =
    !voiceRecording &&
    (
      (busy && (voiceStage === "transcribing" || voiceStage === "thinking")) ||
      voicePlaying ||
      voiceStage === "speaking"
    );
  const voiceSessionModeLabel =
    (voiceWakeArmed ? voiceSessionMode : null) === "conv"
      ? "Conv RAG (с контекстом)"
      : "Q&A (без контекста)";
  const showVoiceModal =
    !voiceModalHidden &&
    (voiceWakeArmed || voiceRecording || voicePlaying || voiceStage === "transcribing" || voiceStage === "thinking" || voiceStage === "speaking");
  const voiceModalQuestionText = (voiceVisualQuestion || "").trim() || (voiceRecording ? "Слушаю..." : "Распознаю вопрос...");
  const voiceModalAnswerText = sanitizeAssistantAnswer(
    voiceVisualAnswer || "",
    Array.isArray(lastVoiceResult?.sources) ? lastVoiceResult.sources : [],
  ) || (
    voiceStage === "thinking"
      ? "Готовлю ответ..."
      : voiceStage === "speaking"
        ? "Озвучиваю ответ..."
        : "Ответ появится здесь"
  );
  const voiceOrbStyle = {
    "--voice-level": String(Number.isFinite(voiceVisualLevel) ? Math.max(0, Math.min(1, voiceVisualLevel)) : 0),
  };
  const answerModeLabel = ANSWER_MODE_OPTIONS.find((opt) => opt.value === questionMode)?.label || "Баланс";
  const answerLengthLabel = ANSWER_LENGTH_OPTIONS.find((opt) => opt.value === answerLength)?.label || "Средний";
  const knowledgeModeLabel = KNOWLEDGE_MODE_OPTIONS.find((opt) => opt.value === knowledgeMode)?.label || "Только документ";
  const textScopeLabel = TEXT_SCOPE_OPTIONS.find((opt) => opt.value === textChatScope)?.label || "Авто";
  const summaryContextLabel = useSummaryContext ? "вкл" : "выкл";

  function handleClearChat() {
    streamEpochRef.current += 1;
    autoSourceMessageKeyRef.current = "";
    setBusy(false);
    setMessages([]);
    setQuestion("");
    clearChatHistory(threadId).catch((e) => {
      onError?.(e.message || "Не удалось очистить историю на сервере");
    });
  }

  function findNearestUserQuestion(messageIdx) {
    for (let i = messageIdx - 1; i >= 0; i -= 1) {
      const m = messages[i];
      if (!m || m.role !== "user") continue;
      const txt = String(m.text || "").trim();
      if (txt) return txt;
    }
    return "";
  }

  async function handlePinAssistantMessage(messageIdx, message) {
    if (!onPinQa) return;
    const projectId = String(activeProjectContext?.project_id || "").trim();
    if (!projectId) {
      onError?.("Сначала выберите активный набор документов, чтобы закрепить Q&A.");
      return;
    }
    const pinKey = String(message?.id || `idx:${messageIdx}`);
    const citations = Array.isArray(message?.citations) ? message.citations : [];
    const parsed = splitReasoning(message?.text || "");
    const answer = sanitizeAssistantAnswer(parsed.answer || "", citations);
    if (!String(answer || "").trim() || String(message?.meta || "") === "Печатает…") return;
    const question = findNearestUserQuestion(messageIdx);
    const meta = String(message?.meta || "").trim();
    const modeHint = (() => {
      const t = meta.toLowerCase();
      if (t.includes("сравнение")) return "compare";
      if (t.includes("conv")) return "conv";
      return mode || "qa";
    })();

    setPinningMessages((prev) => ({ ...prev, [pinKey]: true }));
    try {
      await onPinQa({
        question,
        answer,
        citations,
        mode: modeHint,
        meta,
      });
      setPinnedMessages((prev) => ({ ...prev, [pinKey]: true }));
    } catch (e) {
      onError?.(e?.message || "Не удалось закрепить ответ");
    } finally {
      setPinningMessages((prev) => {
        const next = { ...prev };
        delete next[pinKey];
        return next;
      });
    }
  }

  return (
    <aside className="card chat-panel">
      <div className="chat-header">
        <h3>Чат по документам</h3>
        <div className="chat-mode-toggle">
          <div className="chat-toolbar-row chat-toolbar-primary">
            <div className="chat-toolbar-group chat-toolbar-actions">
              {!liteMode && (
                <button
                  className={`secondary small chat-settings-toggle ${showAdvancedControls ? "is-active" : ""}`.trim()}
                  type="button"
                  onClick={() => setShowAdvancedControls((prev) => !prev)}
                  title={showAdvancedControls ? "Скрыть расширенные настройки" : "Показать расширенные настройки"}
                  aria-label={showAdvancedControls ? "Скрыть расширенные настройки" : "Показать расширенные настройки"}
                  aria-expanded={showAdvancedControls}
                >
                  <ToolbarIcon name="settings" />
                  <span>Настройки ответа</span>
                </button>
              )}
              <button
                className="icon-btn secondary"
                type="button"
                onClick={handleClearChat}
                title="Очистить историю чата"
                aria-label="Очистить историю чата"
              >
                <ToolbarIcon name="trash" />
              </button>
            </div>
          </div>
          {!liteMode && showAdvancedControls && (
            <div className="chat-settings-panel" role="group" aria-label="Настройки ответа">
              <div className="chat-settings-head">
                <div className="chat-settings-title-wrap">
                  <div className="chat-settings-title">Настройки ответа</div>
                  <div className="chat-settings-subtitle">
                    Настройки применяются к текущему ответу и не меняют документ или набор документов.
                  </div>
                </div>
              </div>
              <div className="chat-settings-grid">
                <div className="chat-settings-row chat-settings-row-toggles">
                  <label className="chat-control-item chat-control-toggle" title="Только подтвержденные источниками ответы">
                    <input
                      type="checkbox"
                      checked={strictSources}
                      onChange={(e) => setStrictSources(e.target.checked)}
                    />
                    <span>Строго по источникам</span>
                  </label>
                  <label className="chat-control-item chat-control-toggle" title="Добавлять саммари документа как вспомогательный контекст для лучшей навигации по чанкам">
                    <input
                      type="checkbox"
                      checked={useSummaryContext}
                      onChange={(e) => setUseSummaryContext(e.target.checked)}
                    />
                    <span>Использовать саммари</span>
                  </label>
                </div>
                <div className="chat-settings-row chat-settings-row-selects">
                  {mode !== "compare" && (
                    <label className="chat-control-item chat-control-stack" title="Определяет, можно ли добавлять внешние знания модели сверх документа">
                      <span>Опора ответа</span>
                      <select
                        value={knowledgeMode}
                        onChange={(e) => setKnowledgeMode(normalizeKnowledgeMode(e.target.value))}
                        disabled={busy}
                        aria-label="Опора ответа"
                      >
                        {KNOWLEDGE_MODE_OPTIONS.map((opt) => (
                          <option key={opt.value} value={opt.value}>{opt.label}</option>
                        ))}
                      </select>
                    </label>
                  )}
                  <label className="chat-control-item chat-control-stack" title="Стиль формирования ответа">
                    <span>Режим ответа</span>
                    <select
                      value={questionMode}
                      onChange={(e) => setQuestionMode(normalizeQuestionMode(e.target.value))}
                      disabled={busy}
                      aria-label="Режим ответа"
                    >
                      {ANSWER_MODE_OPTIONS.map((opt) => (
                        <option key={opt.value} value={opt.value}>{opt.label}</option>
                      ))}
                    </select>
                  </label>
                  <label className="chat-control-item chat-control-stack" title="Желаемая длина ответа">
                    <span>Длина</span>
                    <select
                      value={answerLength}
                      onChange={(e) => setAnswerLength(e.target.value)}
                      disabled={busy}
                      aria-label="Длина ответа"
                    >
                      {ANSWER_LENGTH_OPTIONS.map((opt) => (
                        <option key={opt.value} value={opt.value}>{opt.label}</option>
                      ))}
                    </select>
                  </label>
                  <label className="chat-control-item chat-control-stack" title="Контекст текстового чата">
                    <span>Контекст чата</span>
                    <select
                      value={textChatScope}
                      onChange={(e) => setTextChatScope(normalizeTextChatScope(e.target.value))}
                      disabled={busy}
                      aria-label="Контекст текстового чата"
                    >
                      {TEXT_SCOPE_OPTIONS.map((opt) => (
                        <option key={opt.value} value={opt.value}>{opt.label}</option>
                      ))}
                    </select>
                  </label>
                </div>
              </div>
              {mode !== "compare" && knowledgeMode === "hybrid_model" && (
                <div className="chat-knowledge-note">
                  Внешние предложения модели будут помечаться прямо в тексте. Если включен строгий режим, он относится только к части ответа по документу.
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {mode === "compare" ? (
        <>
          {activeProjectContext?.project_id && (
            <div className="chat-compare-project">
              <div className="chat-compare-project-meta">
                <strong>Активный набор документов: {activeProjectContext.name || activeProjectContext.project_id}</strong>
                <span>
                  {(activeProjectDocIds.length || externalCompareSelectedIds.length || 0)} док.
                  {activeProjectContext.is_dirty ? " · выбор в панели «Документы» изменён" : " · используется по умолчанию"}
                </span>
              </div>
              <button
                type="button"
                className="secondary small"
                onClick={() => applyExactCompareSelection(externalCompareSelectedIds)}
                disabled={busy || externalCompareSelectedIds.length < 2}
                title={
                  externalCompareSelectedIds.length < 2
                    ? "Для сравнения в наборе должно быть минимум 2 документа"
                    : "Сбросить выбор документов в сравнении к активному набору или текущему выбору в панели «Документы»"
                }
              >
                Применить набор
              </button>
            </div>
          )}
          <div className="chat-doc-controls">
            <input
              type="text"
              value={docFilter}
              onChange={(e) => setDocFilter(e.target.value)}
              placeholder="Поиск документа по названию или id"
            />
            <div className="chat-doc-actions">
              <button
                type="button"
                className="secondary small"
                onClick={() => {
                  setSelected((prev) => {
                    const next = { ...prev };
                    for (const d of compareDocs) next[d.document_id] = true;
                    return next;
                  });
                }}
              >
                Выбрать все
              </button>
              <button
                type="button"
                className="secondary small"
                onClick={() => {
                  setSelected((prev) => {
                    const next = { ...prev };
                    for (const d of compareDocs) next[d.document_id] = false;
                    return next;
                  });
                }}
              >
                Снять все
              </button>
            </div>
          </div>
          <div className="chat-docs">
            {compareDocs.map((d) => (
              <label key={d.document_id} className="chat-doc-item">
                <input
                  type="checkbox"
                  checked={!!selected[d.document_id]}
                  onChange={(e) => setSelected((prev) => ({ ...prev, [d.document_id]: e.target.checked }))}
                />
                <span title={d.filename}>{d.filename}</span>
              </label>
            ))}
            {compareDocs.length === 0 && <div className="text-muted">Ничего не найдено.</div>}
          </div>
        </>
      ) : liteMode ? (
        <div className="chat-current-doc">
          <div className="chat-current-doc-main">
            Текущий документ: <strong>{docs.find((d) => d.document_id === singleDocId)?.filename || "не выбран"}</strong>
          </div>
          <div className={`chat-current-doc-status ${singleDocStatus.tone}`.trim()}>
            Статус: {singleDocStatus.detail}
          </div>
        </div>
      ) : (
        <div className="chat-current-doc">
          <div className="chat-current-doc-top">
            <div className="chat-current-doc-title">Контекст ответа</div>
          </div>
          <div className="chat-current-doc-summary">
            {textChatTargetIds.length >= 2 ? (
              <>
                <div className="chat-current-doc-main">Контекст ответа: <strong>{textChatContextLabel}</strong></div>
                {voiceChatTargetIds.length >= 2 ? (
                  <div className="chat-current-doc-note">Голосовой режим: тот же набор документов</div>
                ) : singleDocId && (
                  <div className="chat-current-doc-note">
                    Голосовой режим: {docs.find((d) => d.document_id === singleDocId)?.filename || singleDocId}
                  </div>
                )}
              </>
            ) : (
              <>
                {textChatScope === "collection" && !textChatCollectionAvailable ? (
                  <div className="chat-current-doc-main">Контекст ответа: <strong>{textChatContextLabel}</strong></div>
                ) : (
                  <div className="chat-current-doc-main">Текущий документ: <strong>{docs.find((d) => d.document_id === singleDocId)?.filename || "не выбран"}</strong></div>
                )}
              </>
            )}
          </div>
        </div>
      )}

      <div className={`chat-voice-panel${liteMode ? " lite" : ""}`}>
        <div className="chat-voice-main">
          <div className="chat-voice-actions">
            <div className="chat-toolbar-group chat-voice-mode-group" role="tablist" aria-label="Режим чата">
              <button className={mode === "qa" ? "" : "secondary"} onClick={() => setMode("qa")} type="button">Q&A</button>
              <button className={mode === "conv" ? "" : "secondary"} onClick={() => setMode("conv")} type="button">Conv RAG</button>
              {!liteMode && (
                <button className={mode === "compare" ? "" : "secondary"} onClick={() => setMode("compare")} type="button">Сравнение</button>
              )}
            </div>
            <button
              type="button"
              className={`chat-voice-record-btn ${voiceRecording ? "danger" : ""}`.trim()}
              onClick={() => { void toggleVoiceRecording(); }}
              disabled={
                (!voiceRecording && !canBargeIn && busy) ||
                (!voiceRecording && (!voiceSupported || mode === "compare" || !voicePrimaryDocId))
              }
              title={
                mode === "compare"
                  ? "Голосовой режим пока недоступен в режиме сравнения"
                  : !voiceSupported
                    ? "MediaRecorder недоступен в этом браузере"
                    : !voicePrimaryDocId
                      ? (textChatScope === "collection"
                        ? "Для голосового режима по набору документов выберите минимум 2 документа"
                        : "Выберите документ")
                      : voiceRecording
                        ? "Остановить запись вопроса"
                        : canBargeIn
                          ? "Перебить текущий ответ и начать новую запись"
                          : "Начать запись вопроса"
              }
            >
              {voiceRecording ? "Остановить запись" : (canBargeIn ? "Перебить и записать" : "Начать запись")}
            </button>
            <div className={`chat-voice-status stage-${voiceStage}`}>
              <span className="chat-voice-dot" />
              <span>{voiceStageLabel}</span>
            </div>
            <button
              type="button"
              className="icon-btn secondary"
              onClick={() => stopVoicePlayback({ suppressFuture: true })}
              disabled={!voicePlaying}
              title="Остановить озвучку"
              aria-label="Остановить озвучку"
            >
              <ToolbarIcon name="stop" />
            </button>
            {!liteMode && (
              <>
                <button
                  type="button"
                  className="icon-btn secondary"
                  onClick={() => { void retryLastVoiceRequest(); }}
                  disabled={busy || voiceRecording || !voiceRetryInfo?.retryable || !lastVoiceRequestRef.current}
                  title="Повторить последний голосовой запрос без новой записи"
                  aria-label="Повторить голосовой запрос"
                >
                  <ToolbarIcon name="retry" />
                </button>
                <button
                  type="button"
                  className={`icon-btn ${voiceWakeArmed ? "" : "secondary"}`}
                  onClick={toggleVoiceAssistantWakeSession}
                  disabled={mode === "compare" || !voicePrimaryDocId || !voiceWakeSupported}
                  title={
                    !voiceWakeSupported
                      ? "Wake-word требует локальный backend (WebSocket) и доступ к микрофону"
                      : mode === "compare"
                        ? "Доступно только в Q&A / Conv RAG"
                        : !voicePrimaryDocId
                          ? (textChatScope === "collection"
                            ? "Для голосового режима по набору документов выберите минимум 2 документа"
                            : "Выберите документ")
                          : voiceWakeArmed
                            ? "Выключить фонового ассистента"
                            : `Включить фонового ассистента (wake-word: ${voiceWakeWord})`
                  }
                  aria-label={voiceWakeArmed ? "Выключить ассистента" : "Включить ассистента"}
                  aria-pressed={voiceWakeArmed}
                >
                  <ToolbarIcon name="assistant" />
                </button>
              </>
            )}
          </div>
        </div>
        {!liteMode && voiceRetryInfo?.retryable && (
          <div className="chat-voice-retry-note">
            Доступен повтор последнего запроса
            {voiceRetryInfo.stage ? ` (${voiceRetryInfo.stage.toUpperCase()})` : ""}.
          </div>
        )}
      </div>

      {showVoiceModal && (
        <div className="chat-voice-modal-backdrop" aria-live="polite">
          <section className={`chat-voice-modal stage-${voiceStage}`} role="dialog" aria-label="Голосовой режим">
            <div className="chat-voice-modal-head">
              <div className="chat-voice-modal-head-stack">
                <div className="chat-voice-modal-stage">
                  <span className="chat-voice-dot" />
                  <span>{voiceStageLabel}</span>
                </div>
                {voiceWakeArmed && (
                  <div className={`chat-voice-modal-substatus wake-${voiceWakeStatus}`}>
                    Wake-word: <strong>{voiceWakeWord}</strong>
                    {" · "}
                    {voiceWakeStatus === "listening"
                      ? "слушаю"
                      : voiceWakeStatus === "heard"
                        ? "услышал"
                        : voiceWakeStatus === "error"
                          ? "ошибка"
                          : "выкл"}
                    {" · "}
                    {voiceSessionModeLabel}
                    {voiceWakeBargeInEnabled ? " · wake-barge-in вкл" : ""}
                  </div>
                )}
              </div>
              <button
                type="button"
                className="secondary small"
                onClick={() => setVoiceModalHidden(true)}
                title="Скрыть визуал голосового режима"
              >
                Скрыть
              </button>
            </div>

            <div className="chat-voice-modal-text chat-voice-modal-question">
              <div className="chat-voice-modal-caption">Вопрос</div>
              <div className="chat-voice-modal-body">{voiceModalQuestionText}</div>
            </div>

            <div className="chat-voice-orb-wrap">
              <div className="chat-voice-orb-stack">
                <div className="chat-voice-spectrum" style={voiceOrbStyle} aria-hidden="true">
                  <span className="chat-voice-mesh-band mesh-a" />
                  <span className="chat-voice-mesh-band mesh-b" />
                  <span className="chat-voice-mesh-band mesh-c" />
                  <span className="chat-voice-mesh-band mesh-d" />
                </div>
                <div className={`chat-voice-orb stage-${voiceStage}`} style={voiceOrbStyle}>
                  <span className="chat-voice-orb-core" />
                </div>
              </div>
            </div>

            <div className="chat-voice-modal-text chat-voice-modal-answer">
              <div className="chat-voice-modal-caption">Ответ</div>
              <div className="chat-voice-modal-body">{voiceModalAnswerText}</div>
            </div>
          </section>
        </div>
      )}

      {lastVoiceResult && (
        <details className="chat-voice-sources" open>
          <summary>
            Источники voice-ответа
            {Array.isArray(lastVoiceResult.sources) ? ` · ${lastVoiceResult.sources.length}` : ""}
            {lastVoiceResult.chat_mode === "conv" ? " · Conv RAG" : " · Q&A"}
          </summary>
          <div className="chat-voice-sources-meta">
            <span title={lastVoiceResult.question_text || ""}>
              Вопрос: {(lastVoiceResult.question_text || "").slice(0, 160)}
              {(lastVoiceResult.question_text || "").length > 160 ? "..." : ""}
            </span>
            {typeof lastVoiceResult.confidence === "number" && (
              <span title={CONFIDENCE_TOOLTIPS.reliability}>Надежность: {Math.round(lastVoiceResult.confidence * 100)}%</span>
            )}
            {lastVoiceResult.confidence_breakdown && (
              <>
                <span title={CONFIDENCE_TOOLTIPS.retrieval}>Поиск: {Math.round((lastVoiceResult.confidence_breakdown.retrieval_quality || 0) * 100)}%</span>
                <span title={CONFIDENCE_TOOLTIPS.coverage}>Покрытие: {Math.round((lastVoiceResult.confidence_breakdown.evidence_coverage || 0) * 100)}%</span>
                <span title={CONFIDENCE_TOOLTIPS.grounding}>Опора: {Math.round((lastVoiceResult.confidence_breakdown.answer_grounding || 0) * 100)}%</span>
              </>
            )}
          </div>
          {Array.isArray(lastVoiceResult.sources) && lastVoiceResult.sources.length > 0 ? (
            <>
              <div className="chat-citations">
                {dedupeCitations(lastVoiceResult.sources, 6).map((c, i) => (
                  <button
                    key={`voice-source-${i}`}
                    type="button"
                    className={`chat-citation${activeVoiceSource === i ? " is-active" : ""}`}
                    title={c.text || "Текст фрагмента недоступен"}
                    onClick={() => {
                      setActiveVoiceSource(i);
                      openSourceCitation(c);
                    }}
                  >
                    {formatCitationChipLabel(c)}
                  </button>
                ))}
              </div>
            </>
          ) : (
            <div className="chat-voice-sources-empty">Источник(и) для последнего voice-ответа не найдены.</div>
          )}
        </details>
      )}

      <div className="chat-messages">
        {messages.length === 0 && (
          <p className="text-muted">
            {mode === "compare" ? "Задайте вопрос для сравнения выбранных документов." : "Задайте вопрос по текущему документу."}
          </p>
        )}
        {messages.map((m, idx) => {
          const isAssistant = m.role === "assistant";
          const parsed = isAssistant ? splitReasoning(m.text || "") : null;
          const messageCitations = dedupeCitations(m.citations, 8);
          const assistantAnswer = sanitizeAssistantAnswer(String(parsed?.answer || ""), messageCitations);
          const assistantReasoning = String(parsed?.reasoning || "");
          const hasModelKnowledgeContent = !!m.has_model_knowledge_content || hasExternalKnowledgeMarker(assistantAnswer);
          const pinKey = String(m.id || `idx:${idx}`);
          const canPinAssistant =
            !!onPinQa &&
            !!String(activeProjectContext?.project_id || "").trim() &&
            isAssistant &&
            !!assistantAnswer.trim() &&
            String(m.meta || "") !== "Печатает…";
          const isPinning = !!pinningMessages[pinKey];
          const isPinned = !!pinnedMessages[pinKey];
          const messageSourceCitation = resolveMessageSourceCitation(idx, m);
          const canJumpToSource = !!messageSourceCitation;
          return (
          <div
            key={idx}
            className={`chat-message ${m.role}${canJumpToSource ? " has-source" : ""}`}
            onClick={(event) => {
              if (!canJumpToSource) return;
              handleMessageSourceJump(event, idx, m);
            }}
            title={canJumpToSource ? "Клик: перейти к источнику ответа" : undefined}
          >
            <div className="chat-role-row">
              <div className="chat-role">{m.role === "user" ? "Вы" : "Ассистент"}</div>
              {canPinAssistant && (
                <button
                  type="button"
                  className={`secondary small chat-pin-btn ${isPinned ? "is-pinned" : ""}`}
                  onClick={() => { void handlePinAssistantMessage(idx, m); }}
                  disabled={isPinning}
                  title={
                    isPinned
                      ? "Ответ уже закреплён в заметках активного набора документов"
                      : `Закрепить Q&A в заметках: ${activeProjectContext?.name || activeProjectContext?.project_id}`
                  }
                >
                  {isPinning ? "Сохранение…" : (isPinned ? "Закреплено" : "В заметки")}
                </button>
              )}
            </div>
            {isAssistant ? (
              <>
                {hasModelKnowledgeContent && <div className="chat-inline-badge">Вне документа</div>}
                <div className="chat-text markdown-body" dangerouslySetInnerHTML={{ __html: markdownToHtml(assistantAnswer || "") }} />
                {assistantReasoning && (
                  <details className="chat-think">
                    <summary>Рассуждение модели</summary>
                    <div className="chat-think-body markdown-body" dangerouslySetInnerHTML={{ __html: markdownToHtml(assistantReasoning) }} />
                  </details>
                )}
              </>
            ) : (
              <div className="chat-text">{m.text}</div>
            )}
            {m.meta && <div className="chat-meta">{m.meta}</div>}
            {messageCitations.length > 0 && (
              <div className="chat-citations">
                {messageCitations.slice(0, 4).map((c, i) => {
                  const id = `${idx}:${i}`;
                  const isActive = activeCitation === id;
                  return (
                    <button
                      key={i}
                      type="button"
                      className={`chat-citation${isActive ? " is-active" : ""}`}
                      title={c.text || "Текст фрагмента недоступен"}
                      onClick={() => {
                        setActiveCitation(id);
                        openSourceCitation(c);
                      }}
                    >
                      {formatCitationChipLabel(c)}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        )})}
      </div>

      <div className="chat-input">
        <div className="chat-input-meta">
          <span className="chat-input-caption">
            {mode === "compare" ? "Вопрос для сравнения документов" : "Текстовый запрос"}
          </span>
          <span className="chat-input-shortcut">Enter: отправить · Shift+Enter: новая строка</span>
        </div>
        <div className="chat-input-row">
          <textarea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key !== "Enter") return;
              if (e.shiftKey) return;
              if (e.nativeEvent?.isComposing) return;
              e.preventDefault();
              if (!busy && !voiceRecording && question.trim()) {
                handleSend();
              }
            }}
            disabled={voiceRecording}
            placeholder={
              mode === "compare"
                ? "Что сравнить между документами?"
                : mode === "conv"
                  ? "Вопрос с учетом истории диалога"
                  : "Ваш вопрос по текущему документу"
            }
            rows={3}
          />
          <button type="button" onClick={handleSend} disabled={busy || voiceRecording || !question.trim()}>
            {busy ? "Отправка…" : "Отправить"}
          </button>
        </div>
      </div>

      <SourceViewerModal
        open={!liteMode && !!sourceViewerCitation}
        citation={sourceViewerCitation}
        onClose={() => setSourceViewerCitation(null)}
        onError={onError}
      />
    </aside>
  );
}

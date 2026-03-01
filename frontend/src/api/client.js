/* API client for Open NotebookLM backend */

const BASE = "/api";

function decodeHtmlEntities(text) {
  return String(text || "")
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&quot;/gi, '"')
    .replace(/&#39;/gi, "'");
}

function extractHtmlErrorText(body) {
  const src = String(body || "");
  if (!src) return "";
  const title = src.match(/<title[^>]*>([\s\S]*?)<\/title>/i)?.[1];
  const h1 = src.match(/<h1[^>]*>([\s\S]*?)<\/h1>/i)?.[1];
  const extracted = decodeHtmlEntities(title || h1 || "")
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return extracted;
}

function normalizeHttpErrorMessage(status, message, detailObj) {
  const raw = String(message || "").trim();
  const code = String(detailObj?.code || "").trim().toUpperCase();
  const stage = String(detailObj?.stage || "").trim().toLowerCase();

  if (status === 413 || /request entity too large/i.test(raw)) {
    return "Файл больше 150 MB.";
  }
  if (status === 404 || code === "NOT_FOUND" || /not found/i.test(raw)) {
    return "Документ или фрагмент больше недоступен.";
  }
  if (code === "REQUEST_TIMEOUT" || /timeout|timed out|таймаут/i.test(raw)) {
    return "Источник отвечает слишком долго.";
  }
  if (code === "PARSE_ERROR" || stage === "parse" || /parse|extract text|извлеч/i.test(raw)) {
    return "Не удалось извлечь текст из файла.";
  }
  return raw || `HTTP ${status}`;
}

async function buildHttpError(res) {
  const body = await res.text();
  let parsed;
  try {
    parsed = JSON.parse(body);
  } catch {}
  const htmlText = !parsed ? extractHtmlErrorText(body) : "";
  const detail = parsed?.detail ?? body;
  const detailObj = detail && typeof detail === "object" ? detail : null;
  const rawMessage =
    (detailObj && (detailObj.message || detailObj.detail)) ||
    htmlText ||
    (typeof detail === "string" ? detail : "") ||
    `HTTP ${res.status}`;
  const message = normalizeHttpErrorMessage(res.status, rawMessage, detailObj);
  const err = new Error(message);
  err.status = res.status;
  err.detail = detail;
  if (detailObj) {
    if (detailObj.stage) err.stage = detailObj.stage;
    if (detailObj.hint) err.hint = detailObj.hint;
    if (typeof detailObj.retryable === "boolean") err.retryable = detailObj.retryable;
    if (detailObj.code) err.code = detailObj.code;
    if (detailObj.type) err.type = detailObj.type;
  }
  return err;
}

async function request(path, options = {}) {
  const {
    timeoutMs,
    signal,
    ...fetchOptions
  } = options || {};

  const timeout = Number(timeoutMs);
  const hasTimeout = Number.isFinite(timeout) && timeout > 0;
  const controller = hasTimeout && !signal ? new AbortController() : null;
  const effectiveSignal = signal || controller?.signal;
  let timeoutId = null;

  if (controller) {
    timeoutId = setTimeout(() => {
      controller.abort();
    }, timeout);
  }

  try {
    const res = await fetch(`${BASE}${path}`, {
      ...fetchOptions,
      signal: effectiveSignal,
    });
    if (!res.ok) {
      throw await buildHttpError(res);
    }
    return res.json();
  } catch (err) {
    if (controller?.signal?.aborted) {
      const timeoutErr = new Error("Источник отвечает слишком долго.");
      timeoutErr.code = "REQUEST_TIMEOUT";
      throw timeoutErr;
    }
    throw err;
  } finally {
    if (timeoutId != null) clearTimeout(timeoutId);
  }
}

function safeString(value) {
  const v = String(value ?? "").trim();
  return v || "";
}

function safeIntOrNull(value) {
  if (value === null || value === undefined || value === "") return null;
  const n = Number.parseInt(String(value), 10);
  return Number.isFinite(n) ? n : null;
}

function quoteFromText(text) {
  const lines = String(text || "")
    .split(/\r?\n/g)
    .map((line) => line.trim())
    .filter(Boolean);
  for (const line of lines) {
    if (line.length >= 18) return line.slice(0, 260);
  }
  return (lines[0] || "").slice(0, 260);
}

function normalizeSourceLocator(rawLocator, citation) {
  const c = citation && typeof citation === "object" ? citation : {};
  const loc = rawLocator && typeof rawLocator === "object" ? rawLocator : {};
  const next = { ...loc };
  if (next.page == null && c.page != null) next.page = c.page;
  if (!next.section_path && c.section_path) next.section_path = c.section_path;
  if (!next.anchor && c.anchor) next.anchor = c.anchor;
  if (!next.caption && c.caption) next.caption = c.caption;
  if (!next.source_type && c.source_type) next.source_type = c.source_type;
  if (!next.quote) next.quote = quoteFromText(c.text || "");
  if (!next.kind) {
    const st = safeString(next.source_type || c.source_type).toLowerCase();
    const anc = safeString(next.anchor || c.anchor).toLowerCase();
    if (st.includes("pdf") || anc.startsWith("pdf:")) next.kind = "pdf";
    else if (st.includes("pptx") || anc.startsWith("pptx:")) next.kind = "pptx";
    else if (st.includes("docx") || anc.startsWith("docx:")) next.kind = "docx";
    else next.kind = "text";
  }
  return next;
}

function safeIdToken(value) {
  const raw = safeString(value);
  if (!raw) return "na";
  return raw.replace(/[^a-zA-Z0-9._:-]+/g, "_") || "na";
}

function parseAnchorMeta(anchorId) {
  const raw = safeString(anchorId);
  if (!raw) return { page: null, slide: null };
  const pageMatch = raw.match(/:p(-?\d+)/i);
  const slideMatch = raw.match(/:s(-?\d+)/i);
  return {
    page: pageMatch ? safeIntOrNull(pageMatch[1]) : null,
    slide: slideMatch ? safeIntOrNull(slideMatch[1]) : null,
  };
}

function buildFallbackEvidenceId(citation) {
  const c = citation && typeof citation === "object" ? citation : {};
  const doc = safeIdToken(c.document_id);
  const chunk = safeIdToken(c.chunk_id);
  const idx = typeof c.chunk_index === "number" && c.chunk_index >= 0 ? String(c.chunk_index) : "na";
  return `ev:${doc}:${chunk}:${idx}`;
}

function buildFallbackAnchorId(citation, locator) {
  const c = citation && typeof citation === "object" ? citation : {};
  const loc = locator && typeof locator === "object" ? locator : {};
  const doc = safeIdToken(c.document_id);
  const chunk = safeIdToken(c.chunk_id);
  const start = safeIntOrNull(loc.char_start);
  const end = safeIntOrNull(loc.char_end);
  if (start !== null && start >= 0) {
    const len = end !== null && end > start ? end - start : 1;
    return `a:${doc}:${chunk}:o${start}:${len}`;
  }
  const page = safeIntOrNull(loc.page ?? c.page);
  if (page !== null) return `a:${doc}:${chunk}:p${page}`;
  const slide = safeIntOrNull(loc.slide);
  if (slide !== null) return `a:${doc}:${chunk}:s${slide}`;
  return `a:${doc}:${chunk}:legacy`;
}

export function normalizeCitation(rawCitation) {
  if (!rawCitation || typeof rawCitation !== "object") return null;
  const c = { ...rawCitation };
  const documentId = safeString(c.document_id);
  const chunkId = safeString(c.chunk_id);
  const chunkIndex = safeIntOrNull(c.chunk_index);
  const locator = normalizeSourceLocator(c.source_locator, c);
  const evidenceId = safeString(c.evidence_id) || buildFallbackEvidenceId({ ...c, document_id: documentId, chunk_id: chunkId, chunk_index: chunkIndex });
  const anchorId = safeString(c.anchor_id) || safeString(locator.anchor_id) || buildFallbackAnchorId({ ...c, document_id: documentId, chunk_id: chunkId, chunk_index: chunkIndex }, locator);
  const anchorMeta = parseAnchorMeta(anchorId);
  if (locator.page == null && anchorMeta.page != null) locator.page = anchorMeta.page;
  if (locator.slide == null && anchorMeta.slide != null) locator.slide = anchorMeta.slide;
  locator.anchor_id = anchorId;

  return {
    ...c,
    evidence_id: evidenceId,
    anchor_id: anchorId,
    document_id: documentId,
    chunk_id: chunkId,
    chunk_index: chunkIndex,
    page: c.page ?? locator.page ?? null,
    section_path: safeString(c.section_path || locator.section_path) || null,
    anchor: safeString(c.anchor || locator.anchor) || null,
    caption: safeString(c.caption || locator.caption) || null,
    source_type: safeString(c.source_type || locator.source_type) || null,
    text: String(c.text || ""),
    highlights: Array.isArray(c.highlights) ? c.highlights.map((x) => String(x || "")).filter(Boolean) : [],
    source_locator: locator,
  };
}

export function normalizeCitations(list) {
  if (!Array.isArray(list)) return [];
  return list
    .map((item) => normalizeCitation(item))
    .filter((item) => !!item);
}

function normalizeHistoryMessages(messages) {
  if (!Array.isArray(messages)) return [];
  return messages.map((msg) => {
    if (!msg || typeof msg !== "object") return msg;
    return {
      ...msg,
      citations: normalizeCitations(msg.citations),
    };
  });
}

export async function listDocuments() {
  return request("/documents");
}

export async function listProjects() {
  return request("/projects");
}

export async function getProject(projectId) {
  return request(`/projects/${projectId}`);
}

export async function createProject({ name, document_ids = [] }) {
  return request("/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, document_ids }),
  });
}

export async function updateProject(projectId, body) {
  return request(`/projects/${projectId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
}

export async function deleteProject(projectId) {
  return request(`/projects/${projectId}`, { method: "DELETE" });
}

export async function getProjectNotebook(projectId) {
  return request(`/projects/${projectId}/notebook`);
}

export async function getProjectSettings(projectId) {
  return request(`/projects/${projectId}/settings`);
}

export async function updateProjectSettings(projectId, settings) {
  return request(`/projects/${projectId}/settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ settings: settings || {} }),
  });
}

export async function setProjectNotes(projectId, notes) {
  return request(`/projects/${projectId}/notes`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ notes: String(notes ?? "") }),
  });
}

export async function addProjectPin(projectId, body) {
  return request(`/projects/${projectId}/pins`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
}

export async function deleteProjectPin(projectId, pinId) {
  return request(`/projects/${projectId}/pins/${encodeURIComponent(pinId)}`, {
    method: "DELETE",
  });
}

export async function getDocument(documentId) {
  return request(`/documents/${documentId}`);
}

export function getDocumentSourceUrl(documentId, { page, search, download = false, preview = false } = {}) {
  const queryParts = [];
  if (download) queryParts.push("download=1");
  if (preview) queryParts.push("preview=1");
  const path = `${BASE}/documents/${encodeURIComponent(documentId)}/source`;
  const base = queryParts.length ? `${path}?${queryParts.join("&")}` : path;
  const hashParts = [];
  if (page != null && String(page).trim() !== "") {
    hashParts.push(`page=${encodeURIComponent(String(page))}`);
  }
  if (search != null && String(search).trim() !== "") {
    hashParts.push(`search=${encodeURIComponent(String(search).trim())}`);
  }
  return hashParts.length ? `${base}#${hashParts.join("&")}` : base;
}

export async function getDocumentChunk(documentId, chunkId, { highlight, anchorId } = {}) {
  const params = [];
  const q = String(highlight || "").trim();
  const aid = String(anchorId || "").trim();
  if (q) params.push(`highlight=${encodeURIComponent(q)}`);
  if (aid) params.push(`anchor_id=${encodeURIComponent(aid)}`);
  const query = params.length ? `?${params.join("&")}` : "";
  return request(`/documents/${encodeURIComponent(documentId)}/chunks/${encodeURIComponent(chunkId)}${query}`, {
    timeoutMs: 12000,
  });
}

export async function getDocumentFullText(documentId, {
  start,
  end,
  around,
  maxChars,
  anchorId,
  highlight,
  full = false,
} = {}) {
  const params = [];
  if (start !== undefined && start !== null && String(start).trim() !== "") {
    params.push(`start=${encodeURIComponent(String(start))}`);
  }
  if (end !== undefined && end !== null && String(end).trim() !== "") {
    params.push(`end=${encodeURIComponent(String(end))}`);
  }
  if (around !== undefined && around !== null && String(around).trim() !== "") {
    params.push(`around=${encodeURIComponent(String(around))}`);
  }
  if (maxChars !== undefined && maxChars !== null && String(maxChars).trim() !== "") {
    params.push(`max_chars=${encodeURIComponent(String(maxChars))}`);
  }
  const aid = String(anchorId || "").trim();
  if (aid) params.push(`anchor_id=${encodeURIComponent(aid)}`);
  const q = String(highlight || "").trim();
  if (q) params.push(`highlight=${encodeURIComponent(q)}`);
  if (full) params.push("full=1");
  const query = params.length ? `?${params.join("&")}` : "";
  return request(`/documents/${encodeURIComponent(documentId)}/fulltext${query}`, {
    timeoutMs: 20000,
  });
}

export async function deleteDocument(documentId) {
  return request(`/documents/${documentId}`, { method: "DELETE" });
}

export async function importScriptOnly(script) {
  return request("/import_script", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ script }),
  });
}

export async function uploadFile(file) {
  const form = new FormData();
  form.append("file", file);
  return request("/upload", { method: "POST", body: form });
}

export async function uploadUrl(url) {
  return request("/upload_url", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
}

export async function ingest(documentId) {
  return request(`/ingest/${documentId}`, { method: "POST" });
}

export async function ingestJob(documentId) {
  return request(`/ingest/${documentId}/job`, { method: "POST" });
}

export async function getSummary(documentId) {
  return request(`/summary/${documentId}`);
}

/**
 * Consume SSE stream from GET /summary/{id}/stream. Calls onChunk(partialText) and onDone(summary, sources).
 */
export async function consumeSummaryStream(documentId, { onChunk, onDone, onError }) {
  const res = await fetch(`${BASE}/summary/${documentId}/stream`, { method: "GET" });
  if (!res.ok) {
    const t = await res.text();
    if (onError) onError(new Error(t));
    return;
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  let full = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const lines = buf.split("\n");
    buf = lines.pop() || "";
    for (const line of lines) {
      if (line.startsWith("data: ")) {
        const data = line.slice(6).trim();
        if (!data) continue;
        try {
          const j = JSON.parse(data);
          if (j.chunk != null) {
            full += j.chunk;
            if (onChunk) onChunk(full);
          }
          if (j.done) {
            if (onDone) onDone(j.full || full, j.sources || []);
            return;
          }
          if (j.error) {
            if (onError) onError(new Error(j.error));
            return;
          }
        } catch (_) {}
      }
    }
  }
}

/**
 * Consume SSE stream from POST /podcast_script/{id}/stream. Calls onChunk(partialText) and onDone(scriptArray).
 * @param {object} body - { minutes, style, focus?, voices, scenario?, scenario_options?, generation_mode?, role_llm_map?, outline_plan?, tts_friendly? }.
 * tts_friendly: generate text with stress marks and Russian transcription for TTS.
 */
export async function consumeScriptStream(documentId, body, { onChunk, onDone, onError, onStatus }) {
  const res = await fetch(`${BASE}/podcast_script/${documentId}/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const t = await res.text();
    if (onError) onError(new Error(t));
    return;
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  let full = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const lines = buf.split("\n");
    buf = lines.pop() || "";
    for (const line of lines) {
      if (line.startsWith("data: ")) {
        const data = line.slice(6).trim();
        if (!data) continue;
        try {
          const j = JSON.parse(data);
          if (j.chunk != null) {
            full += j.chunk;
            if (onChunk) onChunk(full);
          }
          if (j.status != null || j.warning != null) {
            if (onStatus) {
              onStatus({
                status: j.status || "",
                message: j.message || "",
                warning: j.warning || "",
                prompt_debug: j.prompt_debug || null,
                turn_progress: j.turn_progress || null,
              });
            }
          }
          if (j.done) {
            if (onDone && j.script) onDone(j.script, {
              knowledge_mode: j.knowledge_mode || "document_only",
              effective_knowledge_mode: j.effective_knowledge_mode || j.knowledge_mode || "document_only",
            });
            return;
          }
          if (j.error) {
            if (onError) onError(new Error(j.error));
            return;
          }
        } catch (_) {}
      }
    }
  }
}

export async function generateScriptOutline(documentId, body) {
  return request(`/podcast_script/${documentId}/outline`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/**
 * @param {object} options - { minutes, style, focus?, voices, scenario?, scenario_options?, generation_mode?, role_llm_map?, outline_plan?, tts_friendly? }.
 * tts_friendly: generate text with stress marks and Russian transcription for TTS.
 */
export async function generateScript(documentId, { minutes, style, focus, voices, scenario, scenario_options, generation_mode, role_llm_map, outline_plan, tts_friendly, knowledge_mode = "document_only" }) {
  return request(`/podcast_script/${documentId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ minutes, style, focus, voices, scenario, scenario_options, generation_mode, role_llm_map, outline_plan, tts_friendly, knowledge_mode }),
  });
}

export async function importScript(documentId, script) {
  return request(`/podcast_script/${documentId}/import`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ script }),
  });
}

export async function getScriptTtsQuality(documentId) {
  return request(`/podcast_script/${documentId}/tts_quality`);
}

export async function getScriptLocks(documentId) {
  return request(`/podcast_script/${documentId}/locks`);
}

export async function saveScriptLocks(documentId, locks) {
  return request(`/podcast_script/${documentId}/locks`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ locks }),
  });
}

export async function previewScriptLine(documentId, { voice, text }) {
  return request(`/podcast_script/${documentId}/preview_line`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ voice, text }),
  });
}

export async function regenerateScriptLine(documentId, { line_index, instruction = "", tts_friendly = true, neighbor_window = 2, doc_top_k = 4 }) {
  return request(`/podcast_script/${documentId}/regenerate_line`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ line_index, instruction, tts_friendly, neighbor_window, doc_top_k }),
  });
}

export async function getScriptTimeline(documentId) {
  return request(`/podcast_script/${documentId}/timeline`);
}

export async function getScriptMetrics(documentId) {
  return request(`/podcast_script/${documentId}/metrics`);
}

export async function getScriptVersions(documentId) {
  return request(`/podcast_script/${documentId}/versions`);
}

export async function getScriptVersion(documentId, versionId) {
  return request(`/podcast_script/${documentId}/versions/${encodeURIComponent(versionId)}`);
}

export async function compareScriptVersions(documentId, { left_version_id, right_version_id }) {
  return request(`/podcast_script/${documentId}/versions/compare`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ left_version_id, right_version_id }),
  });
}

export async function restoreScriptVersion(documentId, versionId) {
  return request(`/podcast_script/${documentId}/versions/${encodeURIComponent(versionId)}/restore`, {
    method: "POST",
  });
}

export async function downloadScriptExport(documentId, format) {
  const res = await fetch(`${BASE}/podcast_script/${documentId}/export/${format}`, { method: "GET" });
  if (!res.ok) {
    const t = await res.text();
    throw new Error(t || "Ошибка экспорта");
  }
  const blob = await res.blob();
  const cd = res.headers.get("content-disposition") || "";
  const m = cd.match(/filename=\"?([^\";]+)\"?/i);
  const filename = (m && m[1]) || `${documentId}_script.${format}`;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export async function generateAudio(documentId) {
  return request(`/podcast_audio/${documentId}`, { method: "POST" });
}

export async function getJob(jobId) {
  return request(`/jobs/${jobId}`);
}

export async function cancelJob(jobId) {
  return request(`/jobs/${jobId}/cancel`, { method: "POST" });
}

export async function retryJob(jobId) {
  return request(`/jobs/${jobId}/retry`, { method: "POST" });
}

export async function runBatchJob({ document_ids, mode, minutes, style, focus, voices, scenario, scenario_options, generation_mode, role_llm_map, tts_friendly, knowledge_mode = "document_only" }) {
  return request("/batch/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ document_ids, mode, minutes, style, focus, voices, scenario, scenario_options, generation_mode, role_llm_map, tts_friendly, knowledge_mode }),
  });
}

export async function exportBatchBundle(document_ids) {
  return request("/batch/export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ document_ids }),
  });
}

export function downloadUrl(filename) {
  return `${BASE}/download/${filename}`;
}

export async function voiceQa(
  documentId,
  {
    audioBlob,
    filename = "question.webm",
    document_ids = null,
    strict_sources = false,
    use_summary_context = false,
    question_mode = "default",
    answer_length = "medium",
    knowledge_mode = "document_only",
    stt_model = "small",
    chat_mode = "qa",
    thread_id = "main-chat",
    history_limit = 12,
    with_tts = true,
    signal,
  } = {},
) {
  const form = new FormData();
  form.append("audio", audioBlob, filename);
  if (Array.isArray(document_ids) && document_ids.length > 0) {
    form.append("document_ids", JSON.stringify(document_ids.map((x) => String(x || "").trim()).filter(Boolean)));
  }
  form.append("strict_sources", strict_sources ? "true" : "false");
  form.append("use_summary_context", use_summary_context ? "true" : "false");
  form.append("question_mode", question_mode || "default");
  form.append("answer_length", answer_length || "medium");
  form.append("knowledge_mode", knowledge_mode || "document_only");
  form.append("stt_model", stt_model || "small");
  form.append("chat_mode", chat_mode || "qa");
  form.append("thread_id", thread_id || "main-chat");
  form.append("history_limit", String(history_limit ?? 12));
  form.append("with_tts", with_tts ? "true" : "false");

  const controller = new AbortController();
  let timeoutAbort = false;
  const timer = setTimeout(() => {
    timeoutAbort = true;
    controller.abort();
  }, 5 * 60 * 1000);
  const onExternalAbort = () => controller.abort();
  if (signal) {
    if (signal.aborted) {
      clearTimeout(timer);
      const err = new Error("Voice Q&A запрос прерван");
      err.code = "voice_qa_aborted";
      err.stage = "client";
      err.retryable = false;
      throw err;
    }
    signal.addEventListener("abort", onExternalAbort, { once: true });
  }
  try {
    const res = await fetch(`${BASE}/voice_qa/${encodeURIComponent(documentId)}`, {
      method: "POST",
      body: form,
      signal: controller.signal,
    });
    if (!res.ok) {
      throw await buildHttpError(res);
    }
    const payload = await res.json();
    return {
      ...payload,
      sources: normalizeCitations(payload?.sources),
      citations: normalizeCitations(payload?.citations),
    };
  } catch (e) {
    if (e?.name === "AbortError") {
      const err = new Error(timeoutAbort ? "Voice Q&A: таймаут (5 мин)." : "Voice Q&A запрос прерван");
      err.code = timeoutAbort ? "voice_qa_timeout" : "voice_qa_aborted";
      err.stage = timeoutAbort ? "unknown" : "client";
      err.retryable = !!timeoutAbort;
      throw err;
    }
    throw e;
  } finally {
    clearTimeout(timer);
    if (signal) {
      try { signal.removeEventListener("abort", onExternalAbort); } catch (_) {}
    }
  }
}

function buildStreamPayloadError(payload, fallbackMessage = "Ошибка stream") {
  const detailObj = payload && typeof payload === "object" ? payload : null;
  const message =
    (detailObj && (detailObj.message || detailObj.error || detailObj.detail)) ||
    fallbackMessage;
  const err = new Error(message);
  if (detailObj) {
    if (detailObj.stage) err.stage = detailObj.stage;
    if (detailObj.hint) err.hint = detailObj.hint;
    if (typeof detailObj.retryable === "boolean") err.retryable = detailObj.retryable;
    if (detailObj.code) err.code = detailObj.code;
    if (detailObj.type) err.type = detailObj.type;
    err.detail = detailObj;
  }
  return err;
}

export async function consumeVoiceQaStream(
  documentId,
  {
    audioBlob,
    filename = "question.webm",
    document_ids = null,
    strict_sources = false,
    use_summary_context = false,
    question_mode = "default",
    answer_length = "medium",
    knowledge_mode = "document_only",
    stt_model = "small",
    chat_mode = "qa",
    thread_id = "main-chat",
    history_limit = 12,
    with_tts = true,
    signal,
  } = {},
  { onStatus, onChunk, onDone, onError } = {},
) {
  const form = new FormData();
  form.append("audio", audioBlob, filename);
  if (Array.isArray(document_ids) && document_ids.length > 0) {
    form.append("document_ids", JSON.stringify(document_ids.map((x) => String(x || "").trim()).filter(Boolean)));
  }
  form.append("strict_sources", strict_sources ? "true" : "false");
  form.append("use_summary_context", use_summary_context ? "true" : "false");
  form.append("question_mode", question_mode || "default");
  form.append("answer_length", answer_length || "medium");
  form.append("knowledge_mode", knowledge_mode || "document_only");
  form.append("stt_model", stt_model || "small");
  form.append("chat_mode", chat_mode || "qa");
  form.append("thread_id", thread_id || "main-chat");
  form.append("history_limit", String(history_limit ?? 12));
  form.append("with_tts", with_tts ? "true" : "false");

  const controller = new AbortController();
  let timeoutAbort = false;
  const timer = setTimeout(() => {
    timeoutAbort = true;
    controller.abort();
  }, 5 * 60 * 1000);
  const onExternalAbort = () => controller.abort();
  if (signal) {
    if (signal.aborted) {
      clearTimeout(timer);
      const err = new Error("Voice Q&A stream прерван");
      err.code = "voice_qa_aborted";
      err.stage = "client";
      err.retryable = false;
      throw err;
    }
    signal.addEventListener("abort", onExternalAbort, { once: true });
  }

  try {
    const res = await fetch(`${BASE}/voice_qa/${encodeURIComponent(documentId)}/stream`, {
      method: "POST",
      body: form,
      signal: controller.signal,
    });
    if (!res.ok) {
      onError?.(await buildHttpError(res));
      return;
    }

    const reader = res.body?.getReader?.();
    if (!reader) {
      onError?.(new Error("Voice Q&A stream не поддерживается в этом браузере"));
      return;
    }
    const dec = new TextDecoder();
    let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const lines = buf.split("\n");
      buf = lines.pop() || "";
      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const data = line.slice(6).trim();
        if (!data) continue;
        try {
          const j = JSON.parse(data);
          if (j.status) onStatus?.(j);
          if (j.chunk != null) onChunk?.(j.chunk, j);
          if (j.done) {
            onDone?.({
              ...j,
              sources: normalizeCitations(j.sources),
              citations: normalizeCitations(j.citations),
            });
            return;
          }
          if (j.error) {
            onError?.(buildStreamPayloadError(j, "Ошибка Voice Q&A stream"));
            return;
          }
        } catch (_) {}
      }
    }
    onError?.(new Error("Voice Q&A stream завершился до получения результата"));
  } catch (e) {
    if (e?.name === "AbortError") {
      const err = new Error(timeoutAbort ? "Voice Q&A: таймаут (5 мин)." : "Voice Q&A запрос прерван");
      err.code = timeoutAbort ? "voice_qa_timeout" : "voice_qa_aborted";
      err.stage = timeoutAbort ? "unknown" : "client";
      err.retryable = !!timeoutAbort;
      onError?.(err);
      return;
    }
    onError?.(e);
  } finally {
    clearTimeout(timer);
    if (signal) {
      try { signal.removeEventListener("abort", onExternalAbort); } catch (_) {}
    }
  }
}

export async function getDocumentArtifacts(documentId) {
  return request(`/artifacts/${documentId}`);
}

export async function getSettings() {
  return request("/settings");
}

export async function updateSettings(settings) {
  return request("/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settings),
  });
}

export async function testLMStudio(baseUrl) {
  const suffix = baseUrl ? `?base_url=${encodeURIComponent(baseUrl)}` : "";
  return request(`/settings/test${suffix}`);
}

export async function getVoices() {
  return request("/settings/voices");
}

export async function updateVoices(voicesPayload) {
  return request("/settings/voices", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(voicesPayload),
  });
}

export async function getRoleLlmSettings() {
  return request("/settings/role_llm");
}

export async function updateRoleLlmSettings(payload) {
  return request("/settings/role_llm", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function getMusicSettings() {
  return request("/settings/music");
}

export async function updateMusicSettings(payload) {
  return request("/settings/music", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function listMusicFiles() {
  return request("/settings/music/files");
}

export function musicFileUrl(filename) {
  return `${BASE}/settings/music/file/${encodeURIComponent(filename)}`;
}

export async function uploadMusicFile(slot, file) {
  const form = new FormData();
  form.append("slot", slot);
  form.append("file", file);
  return request("/settings/music/upload", {
    method: "POST",
    body: form,
  });
}

export async function getPostprocessSettings() {
  return request("/settings/postprocess");
}

export async function updatePostprocessSettings(payload) {
  return request("/settings/postprocess", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function getOcrSettings() {
  return request("/settings/ocr");
}

export async function updateOcrSettings(payload) {
  return request("/settings/ocr", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function getVisionIngestSettings() {
  return request("/settings/vision_ingest");
}

export async function updateVisionIngestSettings(payload) {
  return request("/settings/vision_ingest", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function getStyleProfiles() {
  return request("/settings/style_profiles");
}

export async function getScriptScenarios() {
  return request("/settings/scenarios");
}

export async function upsertScriptScenario(payload) {
  return request("/settings/scenarios", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function deleteScriptScenario(scenarioId) {
  return request(`/settings/scenarios/${encodeURIComponent(scenarioId)}`, {
    method: "DELETE",
  });
}

export async function upsertStyleProfile(payload) {
  return request("/settings/style_profiles", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function deleteStyleProfile(profileId) {
  return request(`/settings/style_profiles/${encodeURIComponent(profileId)}`, {
    method: "DELETE",
  });
}

export async function getPronunciationOverrides() {
  return request("/settings/pronunciation");
}

export async function updatePronunciationOverrides(payload) {
  return request("/settings/pronunciation", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function clearDatabase(payload) {
  return request("/settings/database/clear", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

/** Таймаут запроса теста голоса (мс). Генерация одного предложения обычно до 30 с. */
const VOICE_TEST_TIMEOUT_MS = 120000;

export async function testVoice(slot) {
  const controller = new AbortController();
  const id = setTimeout(() => controller.abort(), VOICE_TEST_TIMEOUT_MS);
  try {
    const res = await fetch(`${BASE}/settings/voices/test`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slot }),
      signal: controller.signal,
    });
    clearTimeout(id);
    if (!res.ok) {
      const body = await res.text();
      let detail = body;
      try {
        detail = JSON.parse(body).detail || body;
      } catch {}
      throw new Error(detail);
    }
    return res.json();
  } catch (e) {
    clearTimeout(id);
    if (e.name === "AbortError") throw new Error("Тест голоса: таймаут (2 мин). Попробуйте ещё раз.");
    throw e;
  }
}

export async function queryChat({
  question,
  document_ids,
  document_id,
  strict_sources = false,
  use_summary_context = false,
  question_mode = "default",
  answer_length = "medium",
  knowledge_mode = "document_only",
}) {
  const res = await request("/chat/query", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, document_ids, document_id, strict_sources, use_summary_context, question_mode, answer_length, knowledge_mode }),
  });
  return {
    ...res,
    citations: normalizeCitations(res?.citations),
  };
}

export async function consumeChatStream(
  { question, document_ids, document_id, thread_id, strict_sources = false, use_summary_context = false, question_mode = "default", answer_length = "medium", knowledge_mode = "document_only" },
  { onChunk, onDone, onError },
) {
  const res = await fetch(`${BASE}/chat/query/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, document_ids, document_id, thread_id, strict_sources, use_summary_context, question_mode, answer_length, knowledge_mode }),
  });
  if (!res.ok) {
    const t = await res.text();
    if (onError) onError(new Error(t));
    return;
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  let full = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const lines = buf.split("\n");
    buf = lines.pop() || "";
    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;
      const data = line.slice(6).trim();
      if (!data) continue;
      try {
        const j = JSON.parse(data);
        if (j.chunk != null) {
          full += j.chunk;
          onChunk?.(full);
        }
        if (j.done) {
          onDone?.({
            answer: j.full || full,
            confidence: j.confidence,
            confidence_breakdown: j.confidence_breakdown || null,
            citations: normalizeCitations(j.citations),
            knowledge_mode: j.knowledge_mode || "document_only",
            effective_knowledge_mode: j.effective_knowledge_mode || j.knowledge_mode || "document_only",
            has_model_knowledge_content: !!j.has_model_knowledge_content,
          });
          return;
        }
        if (j.error) {
          onError?.(new Error(j.error));
          return;
        }
      } catch (_) {}
    }
  }
}

export async function consumeConversationalChatStream(
  { question, document_ids, document_id, thread_id, strict_sources = false, use_summary_context = false, question_mode = "default", answer_length = "medium", knowledge_mode = "document_only" },
  { onChunk, onDone, onError },
) {
  const res = await fetch(`${BASE}/chat/query/conversational/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, document_ids, document_id, thread_id, strict_sources, use_summary_context, question_mode, answer_length, knowledge_mode }),
  });
  if (!res.ok) {
    const t = await res.text();
    if (onError) onError(new Error(t));
    return;
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  let full = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const lines = buf.split("\n");
    buf = lines.pop() || "";
    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;
      const data = line.slice(6).trim();
      if (!data) continue;
      try {
        const j = JSON.parse(data);
        if (j.chunk != null) {
          full += j.chunk;
          onChunk?.(full);
        }
        if (j.done) {
          onDone?.({
            answer: j.full || full,
            confidence: j.confidence,
            confidence_breakdown: j.confidence_breakdown || null,
            citations: normalizeCitations(j.citations),
            knowledge_mode: j.knowledge_mode || "document_only",
            effective_knowledge_mode: j.effective_knowledge_mode || j.knowledge_mode || "document_only",
            has_model_knowledge_content: !!j.has_model_knowledge_content,
          });
          return;
        }
        if (j.error) {
          onError?.(new Error(j.error));
          return;
        }
      } catch (_) {}
    }
  }
}

export async function getChatHistory(thread_id = "main-chat", limit = 60) {
  const res = await request(`/chat/history?thread_id=${encodeURIComponent(thread_id)}&limit=${encodeURIComponent(limit)}`);
  return {
    ...res,
    messages: normalizeHistoryMessages(res?.messages),
  };
}

export async function clearChatHistory(thread_id = "main-chat") {
  return request(`/chat/history?thread_id=${encodeURIComponent(thread_id)}`, { method: "DELETE" });
}

export async function compareDocuments({ document_ids, focus }) {
  return request("/compare", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ document_ids, focus }),
  });
}

export async function getQualityReport(documentId) {
  return request(`/quality/${documentId}`);
}

export async function downloadDocumentBundle(documentId) {
  const res = await fetch(`${BASE}/export/${documentId}/bundle`, { method: "GET" });
  if (!res.ok) {
    const t = await res.text();
    throw new Error(t || "Ошибка экспорта bundle");
  }
  const blob = await res.blob();
  const cd = res.headers.get("content-disposition") || "";
  const m = cd.match(/filename=\"?([^\";]+)\"?/i);
  const filename = (m && m[1]) || `${documentId}_bundle.zip`;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export async function downloadReportDocx(documentId) {
  const res = await fetch(`${BASE}/export/${documentId}/report_docx`, { method: "GET" });
  if (!res.ok) {
    const t = await res.text();
    throw new Error(t || "Ошибка экспорта DOCX отчета");
  }
  const blob = await res.blob();
  const cd = res.headers.get("content-disposition") || "";
  const m = cd.match(/filename=\"?([^\";]+)\"?/i);
  const filename = (m && m[1]) || `${documentId}_report.docx`;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

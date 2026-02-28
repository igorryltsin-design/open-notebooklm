export const UI_STATUS_STATES = {
  IDLE: "idle",
  LOADING: "loading",
  READY: "ready",
  ERROR: "error",
};

function clampNonNegativeInt(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return 0;
  return Math.max(0, Math.trunc(n));
}

function hasText(value) {
  return String(value || "").trim().length > 0;
}

function normalizeState(raw) {
  const v = String(raw || "").trim().toLowerCase();
  if (v === UI_STATUS_STATES.LOADING) return UI_STATUS_STATES.LOADING;
  if (v === UI_STATUS_STATES.READY) return UI_STATUS_STATES.READY;
  if (v === UI_STATUS_STATES.ERROR) return UI_STATUS_STATES.ERROR;
  return UI_STATUS_STATES.IDLE;
}

export function mapStatusTone(rawState) {
  const state = normalizeState(rawState);
  if (state === UI_STATUS_STATES.READY) return "is-on";
  if (state === UI_STATUS_STATES.LOADING) return "state-loading";
  if (state === UI_STATUS_STATES.ERROR) return "state-error";
  return "state-idle";
}

export function buildDocumentStatus(input = {}) {
  const documentId = String(input.documentId || "").trim();
  const filename = String(input.filename || "").trim();
  const displayName = filename || documentId || "не выбран";
  const includeLabel = input.includeLabel !== false;
  const loading = !!input.loading || !!input.autoIngesting;
  const ingested = !!input.ingested;
  const chunks = clampNonNegativeInt(input.chunks);
  const errorMessage = String(input.error || "").trim();

  let state = UI_STATUS_STATES.IDLE;
  let detail = includeLabel ? "не выбран" : "не выбран";

  if (!documentId) {
    state = UI_STATUS_STATES.IDLE;
    detail = includeLabel ? "не выбран" : "не выбран";
  } else if (errorMessage) {
    state = UI_STATUS_STATES.ERROR;
    detail = includeLabel ? `${displayName} · ошибка` : "ошибка";
  } else if (loading) {
    state = UI_STATUS_STATES.LOADING;
    detail = includeLabel ? `${displayName} · автоиндексация` : "автоиндексация";
  } else if (ingested) {
    state = UI_STATUS_STATES.READY;
    detail = includeLabel ? `${displayName} · индекс ${chunks} фрагм.` : `индекс ${chunks} фрагм.`;
  } else {
    state = UI_STATUS_STATES.IDLE;
    detail = includeLabel ? `${displayName} · без индекса` : "без индекса";
  }

  return {
    key: "document",
    state,
    title: "Документ",
    detail,
    tone: mapStatusTone(state),
  };
}

export function buildSummaryStatus(input = {}) {
  const summary = String(input.summary || "");
  const streamingSummary = String(input.streamingSummary || "");
  const errorMessage = String(input.error || "").trim();
  const hasStreaming = hasText(streamingSummary);
  const hasSummary = input.isReady == null ? hasText(summary) : !!input.isReady;
  const chars = clampNonNegativeInt(
    input.chars != null ? input.chars : (hasStreaming ? streamingSummary.length : summary.length),
  );
  const sourcesCount = clampNonNegativeInt(input.sourcesCount != null ? input.sourcesCount : input.sources?.length);

  let state = UI_STATUS_STATES.IDLE;
  let detail = "не создано";
  if (errorMessage) {
    state = UI_STATUS_STATES.ERROR;
    detail = "ошибка";
  } else if (hasStreaming) {
    state = UI_STATUS_STATES.LOADING;
    detail = chars > 0 ? `генерация · ${chars} симв.` : "генерация";
  } else if (hasSummary) {
    state = UI_STATUS_STATES.READY;
    const detailParts = [`${chars} симв.`];
    detailParts.push(`${sourcesCount} ист.`);
    detail = `готово · ${detailParts.join(", ")}`;
  }

  return {
    key: "summary",
    state,
    title: "Саммари",
    detail,
    tone: mapStatusTone(state),
  };
}

export function buildScriptStatus(input = {}) {
  const streamingScript = String(input.streamingScript || "");
  const hasStreaming = hasText(streamingScript);
  const errorMessage = String(input.error || "").trim();
  const lines = clampNonNegativeInt(input.lines != null ? input.lines : input.script?.length);
  const chars = clampNonNegativeInt(input.chars != null ? input.chars : streamingScript.length);
  const hasScript = input.isReady == null ? lines > 0 : !!input.isReady;

  let state = UI_STATUS_STATES.IDLE;
  let detail = "не создан";
  if (errorMessage) {
    state = UI_STATUS_STATES.ERROR;
    detail = "ошибка";
  } else if (hasStreaming) {
    state = UI_STATUS_STATES.LOADING;
    detail = chars > 0 ? `генерация · ${chars} симв.` : "генерация";
  } else if (hasScript) {
    state = UI_STATUS_STATES.READY;
    detail = `готово · ${lines} реплик`;
  }

  return {
    key: "script",
    state,
    title: "Скрипт",
    detail,
    tone: mapStatusTone(state),
  };
}

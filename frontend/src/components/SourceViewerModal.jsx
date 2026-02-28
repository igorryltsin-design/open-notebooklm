import React, { useEffect, useMemo, useRef, useState } from "react";
import { getDocumentChunk, getDocumentFullText, getDocumentSourceUrl } from "../api/client";
import "./SourceViewerModal.css";

const LARGE_TEXT_THRESHOLD = 120000;
const LARGE_TEXT_LINE_RADIUS = 240;
const SERVER_WINDOW_REQUEST_CHARS = 90000;
const PREVIEWABLE_DOCUMENT_KINDS = new Set(["pdf", "doc", "docx", "rtf", "odt", "otd", "ppt", "pptx", "djvu", "djv", "djvy"]);


function normalizeSimilarKey(raw) {
  return String(raw || "")
    .toLowerCase()
    .replace(/[«»"“”„‟'`]/g, "")
    .replace(/[.,;:!?()[\]{}<>/\\|]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function isSimilarText(aRaw, bRaw) {
  const a = normalizeSimilarKey(aRaw);
  const b = normalizeSimilarKey(bRaw);
  if (!a || !b) return false;
  if (a === b) return true;
  if (a.length >= 24 && b.includes(a)) return true;
  if (b.length >= 24 && a.includes(b)) return true;
  return false;
}

function dedupeSimilarTexts(values, maxItems = 8) {
  const out = [];
  const seen = [];
  for (const raw of Array.isArray(values) ? values : []) {
    const v = String(raw || "").replace(/\s+/g, " ").trim();
    if (!v) continue;
    const key = normalizeSimilarKey(v);
    if (!key) continue;
    if (seen.some((s) => isSimilarText(s, key))) continue;
    seen.push(key);
    out.push(v);
    if (out.length >= maxItems) break;
  }
  return out;
}

function escapeRegExp(src) {
  return String(src || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function buildLooseRegex(raw, flags = "i") {
  const words = String(raw || "")
    .replace(/\s+/g, " ")
    .trim()
    .split(" ")
    .filter(Boolean)
    .map((w) => escapeRegExp(w));
  if (!words.length) return null;
  return new RegExp(words.join("\\s+"), flags);
}

function stripTruncationMarkers(raw) {
  return String(raw || "")
    .replace(/\(\s*\.\.\.\s*\)\s*$/g, "")
    .replace(/…\s*$/g, "")
    .replace(/\.{3}\s*$/g, "")
    .trim();
}

function findBestHighlightNeedle(text, term) {
  const body = String(text || "");
  const src = stripTruncationMarkers(term);
  if (!body || !src) return "";

  const exact = new RegExp(escapeRegExp(src), "i");
  const loose = buildLooseRegex(src, "i");
  if (exact.test(body) || (loose && loose.test(body))) return src;

  const words = src
    .replace(/[«»"“”„‟'`]/g, " ")
    .replace(/[()[\]{}<>|/\\]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .split(" ")
    .filter((w) => w.length >= 3);

  for (let size = Math.min(10, words.length); size >= 4; size -= 1) {
    for (let start = 0; start <= words.length - size; start += 1) {
      const candidate = words.slice(start, start + size).join(" ").trim();
      if (candidate.length < 20) continue;
      const exactCandidate = new RegExp(escapeRegExp(candidate), "i");
      const looseCandidate = buildLooseRegex(candidate, "i");
      if (exactCandidate.test(body) || (looseCandidate && looseCandidate.test(body))) return candidate;
    }
  }

  for (const w of words) {
    if (w.length < 6) continue;
    const exactWord = new RegExp(escapeRegExp(w), "i");
    const looseWord = buildLooseRegex(w, "i");
    if (exactWord.test(body) || (looseWord && looseWord.test(body))) return w;
  }
  return "";
}

function hasHighlightMatch(text, term) {
  return !!findBestHighlightNeedle(text, term);
}

function normalizeHighlightSpan(text, startRaw, endRaw) {
  const body = String(text || "");
  if (!body) return null;
  const start = Number.isFinite(Number(startRaw)) ? Math.trunc(Number(startRaw)) : null;
  const end = Number.isFinite(Number(endRaw)) ? Math.trunc(Number(endRaw)) : null;
  if (start == null || start < 0 || start >= body.length) return null;
  const safeEnd = end != null && end > start ? Math.min(body.length, end) : Math.min(body.length, start + 1);
  if (safeEnd <= start) return null;
  return { start, end: safeEnd };
}

function resolveOffsetHighlight(text, locator, anchorMeta, term = "") {
  const loc = locator && typeof locator === "object" ? locator : {};
  const meta = anchorMeta && typeof anchorMeta === "object" ? anchorMeta : {};
  const startCandidate = loc.char_start ?? meta.start ?? null;
  const endCandidate = loc.char_end ?? meta.end ?? null;
  const span = normalizeHighlightSpan(text, startCandidate, endCandidate);
  if (!span) return null;
  const checkTerm = String(term || "").trim();
  if (!checkTerm) return span;
  const snippet = String(text || "").slice(span.start, span.end);
  return hasHighlightMatch(snippet, checkTerm) ? span : null;
}

function renderHighlightedText(text, term, offsetSpan = null) {
  const body = String(text || "");
  const primarySpan = normalizeHighlightSpan(body, offsetSpan?.start, offsetSpan?.end);
  if (primarySpan) {
    const nodes = [];
    if (primarySpan.start > 0) nodes.push(body.slice(0, primarySpan.start));
    nodes.push(<mark key="m-offset">{body.slice(primarySpan.start, primarySpan.end)}</mark>);
    if (primarySpan.end < body.length) nodes.push(body.slice(primarySpan.end));
    return nodes;
  }
  const needle = findBestHighlightNeedle(body, term);
  if (!needle) return body;
  const re = buildLooseRegex(needle, "ig") || new RegExp(escapeRegExp(needle), "ig");
  const nodes = [];
  let last = 0;
  let idx = 0;
  let m;
  while ((m = re.exec(body)) !== null) {
    const start = m.index;
    const end = start + m[0].length;
    if (start > last) nodes.push(body.slice(last, start));
    nodes.push(<mark key={`m-${idx}`}>{body.slice(start, end)}</mark>);
    last = end;
    idx += 1;
    if (m[0].length === 0) break;
  }
  if (last < body.length) nodes.push(body.slice(last));
  return nodes.length > 0 ? nodes : body;
}

function buildTextIndex(text) {
  const body = String(text || "");
  if (!body) return { lineStarts: [0], lineCount: 1 };
  const lineStarts = [0];
  for (let i = 0; i < body.length; i += 1) {
    if (body.charCodeAt(i) === 10) lineStarts.push(i + 1);
  }
  return { lineStarts, lineCount: lineStarts.length };
}

function findLineByOffset(lineStarts, offsetRaw) {
  const starts = Array.isArray(lineStarts) && lineStarts.length ? lineStarts : [0];
  const maxIdx = starts.length - 1;
  const offset = Number.isFinite(Number(offsetRaw)) ? Math.max(0, Math.trunc(Number(offsetRaw))) : 0;
  let lo = 0;
  let hi = maxIdx;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (starts[mid] <= offset) lo = mid + 1;
    else hi = mid - 1;
  }
  return Math.max(0, Math.min(maxIdx, lo - 1));
}

function resolveHighlightOffset(text, term) {
  const body = String(text || "");
  const needle = findBestHighlightNeedle(body, term);
  if (!needle) return -1;
  const exact = new RegExp(escapeRegExp(needle), "i");
  const exactMatch = exact.exec(body);
  if (exactMatch) return exactMatch.index;
  const loose = buildLooseRegex(needle, "i");
  const looseMatch = loose ? loose.exec(body) : null;
  return looseMatch ? looseMatch.index : -1;
}

function resolveTextWindow(text, textIndex, term, offsetSpan = null, forceFull = false) {
  const body = String(text || "");
  const total = body.length;
  const normalizedOffset = normalizeHighlightSpan(body, offsetSpan?.start, offsetSpan?.end);
  const isLarge = total > LARGE_TEXT_THRESHOLD;
  if (!body || forceFull || !isLarge) {
    return {
      text: body,
      span: normalizedOffset,
      isLarge,
      isWindowed: false,
      leadingTruncated: false,
      trailingTruncated: false,
    };
  }

  let centerOffset = normalizedOffset?.start;
  if (!Number.isFinite(centerOffset)) {
    const foundOffset = resolveHighlightOffset(body, term);
    centerOffset = foundOffset >= 0 ? foundOffset : 0;
  }
  const starts = textIndex?.lineStarts || [0];
  const centerLine = findLineByOffset(starts, centerOffset);
  const startLine = Math.max(0, centerLine - LARGE_TEXT_LINE_RADIUS);
  const endLineExclusive = Math.min(starts.length, centerLine + LARGE_TEXT_LINE_RADIUS + 1);
  const startOffset = starts[startLine] ?? 0;
  const endOffset = endLineExclusive < starts.length ? starts[endLineExclusive] : total;
  const sliced = body.slice(startOffset, endOffset);
  const localSpan = normalizedOffset
    ? normalizeHighlightSpan(sliced, normalizedOffset.start - startOffset, normalizedOffset.end - startOffset)
    : null;

  return {
    text: sliced,
    span: localSpan,
    isLarge,
    isWindowed: true,
    leadingTruncated: startOffset > 0,
    trailingTruncated: endOffset < total,
  };
}

function sourceKindLabel(kind) {
  const k = String(kind || "").toLowerCase();
  if (k === "pdf") return "PDF";
  if (k === "doc") return "DOC";
  if (k === "docx") return "DOCX";
  if (k === "rtf") return "RTF";
  if (k === "odt" || k === "otd") return "ODT";
  if (k === "ppt") return "PPT";
  if (k === "pptx") return "PPTX";
  if (k === "djvu" || k === "djv" || k === "djvy") return "DJVU";
  if (k === "html") return "HTML";
  return "Текст";
}

function parseAnchorMeta(anchorId) {
  const raw = String(anchorId || "").trim();
  if (!raw) return { page: null, slide: null, start: null, end: null };
  const pageMatch = raw.match(/:p(-?\d+)/i);
  const slideMatch = raw.match(/:s(-?\d+)/i);
  const offsetMatch = raw.match(/:o(-?\d+):(-?\d+)/i);
  const page = pageMatch ? Number.parseInt(pageMatch[1], 10) : null;
  const slide = slideMatch ? Number.parseInt(slideMatch[1], 10) : null;
  const start = offsetMatch ? Number.parseInt(offsetMatch[1], 10) : null;
  const length = offsetMatch ? Number.parseInt(offsetMatch[2], 10) : null;
  const end = Number.isFinite(start) && Number.isFinite(length) && length > 0 ? start + length : null;
  return {
    page: Number.isFinite(page) ? page : null,
    slide: Number.isFinite(slide) ? slide : null,
    start: Number.isFinite(start) ? start : null,
    end: Number.isFinite(end) ? end : null,
  };
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

export default function SourceViewerModal({ open, citation, onClose, onError }) {
  const [loading, setLoading] = useState(false);
  const [resolved, setResolved] = useState(null);
  const [activeTerm, setActiveTerm] = useState("");
  const [viewMode, setViewMode] = useState("text");
  const [fullTextLoading, setFullTextLoading] = useState(false);
  const [fullText, setFullText] = useState("");
  const [fullTextMeta, setFullTextMeta] = useState(null);
  const [showFullLargeText, setShowFullLargeText] = useState(false);
  const textPreviewRef = useRef(null);
  const citationKey = useMemo(() => resolveCitationKey(citation), [citation]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e) => {
      if (e.key === "Escape") onClose?.();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  useEffect(() => {
    if (!open || !citation?.document_id || !citation?.chunk_id) {
      setResolved(null);
      setLoading(false);
      onError?.("");
      return;
    }
    let cancelled = false;
    setLoading(true);
    onError?.("");
    const hint = String(
      (citation.highlights && citation.highlights[0])
      || (citation.source_locator && citation.source_locator.quote)
      || citation.text
      || "",
    ).trim();
    const anchorId = String(citation.anchor_id || "").trim();
    getDocumentChunk(citation.document_id, citation.chunk_id, { highlight: hint, anchorId })
      .then((row) => {
        if (cancelled) return;
        setResolved(row || null);
        onError?.("");
      })
      .catch((e) => {
        if (cancelled) return;
        onError?.(e?.message || "Не удалось открыть фрагмент источника");
        setResolved(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, citation, onError]);

  const merged = useMemo(() => {
    const base = citation && typeof citation === "object" ? citation : {};
    const row = resolved && typeof resolved === "object" ? resolved : {};
    return {
      ...base,
      ...row,
      source_locator: {
        ...(base.source_locator && typeof base.source_locator === "object" ? base.source_locator : {}),
        ...(row.source_locator && typeof row.source_locator === "object" ? row.source_locator : {}),
      },
    };
  }, [citation, resolved]);

  const locator = merged.source_locator || {};
  const fileNameExt = String(merged.filename || citation?.filename || "").toLowerCase();
  const kind = String(locator.kind || merged.kind || "").toLowerCase();
  const anchorMeta = parseAnchorMeta(merged.anchor_id || locator.anchor_id || citation?.anchor_id);
  const page = locator.page ?? merged.page ?? anchorMeta.page;
  const sourceFileUrl = merged.document_id ? getDocumentSourceUrl(merged.document_id) : "";
  const terms = useMemo(() => {
    return dedupeSimilarTexts([
      ...(Array.isArray(citation?.highlights) ? citation.highlights : []),
      locator.quote,
      ...(Array.isArray(merged.highlights) ? merged.highlights : []),
      merged.text,
      merged.caption,
      merged.section_path,
    ], 8);
  }, [citation?.highlights, merged, locator]);
  const fulltextAnchorId = String(merged.anchor_id || locator.anchor_id || citation?.anchor_id || "").trim();
  const fulltextHint = String(
    (Array.isArray(citation?.highlights) && citation.highlights[0])
    || locator.quote
    || merged.text
    || "",
  ).trim();

  const inlinePreview = String(merged.text || "").trim();
  const viewerUrl = merged.document_id
    ? getDocumentSourceUrl(merged.document_id, {
        page,
        search: activeTerm || locator.quote || "",
        preview: true,
      })
    : "";
  const sourceExt = String(locator.file_extension || "").toLowerCase() || (fileNameExt.includes(".") ? fileNameExt.split(".").pop() : "");
  const normalizedKind = kind || sourceExt;
  const isPdf = normalizedKind === "pdf";
  const hasDocumentPreview = Boolean(viewerUrl && PREVIEWABLE_DOCUMENT_KINDS.has(normalizedKind));
  const openOriginalUrl = sourceFileUrl;
  const fullDocumentText = String(fullText || "").trim();
  const previewBody = fullDocumentText || inlinePreview;
  const textIndex = useMemo(() => buildTextIndex(previewBody), [previewBody]);
  const showDocument = hasDocumentPreview && viewMode === "document";
  const activeHighlightTerm = String(
    activeTerm
    || locator.quote
    || merged.caption
    || merged.section_path
    || merged.text
    || "",
  ).trim();
  const offsetHighlight = useMemo(
    () => resolveOffsetHighlight(previewBody, locator, anchorMeta, activeHighlightTerm),
    [previewBody, locator, anchorMeta, activeHighlightTerm],
  );
  const textWindow = useMemo(() => {
    return resolveTextWindow(previewBody, textIndex, activeHighlightTerm, offsetHighlight, showFullLargeText);
  }, [previewBody, textIndex, activeHighlightTerm, offsetHighlight?.start, offsetHighlight?.end, showFullLargeText]);
  const serverTotalChars = Number(fullTextMeta?.total_chars || 0);
  const serverIsWindowed = Boolean(fullTextMeta?.is_windowed);
  const serverHasMoreBefore = Boolean(fullTextMeta?.has_more_before);
  const serverHasMoreAfter = Boolean(fullTextMeta?.has_more_after);
  const canToggleWindow = Boolean(fullDocumentText) && (serverTotalChars > SERVER_WINDOW_REQUEST_CHARS || textWindow.isLarge);
  const effectiveWindowed = serverIsWindowed || textWindow.isWindowed;
  const effectiveLeadingTruncated = serverHasMoreBefore || textWindow.leadingTruncated;
  const effectiveTrailingTruncated = serverHasMoreAfter || textWindow.trailingTruncated;
  const hasAnyPreviewText = Boolean(fullDocumentText || inlinePreview);

  useEffect(() => {
    if (!open) return;
    setActiveTerm("");
  }, [open, citationKey]);

  useEffect(() => {
    if (!open) return;
    setShowFullLargeText(false);
  }, [open, citationKey, merged.document_id]);

  useEffect(() => {
    if (!open) return;
    setActiveTerm((prev) => {
      if (!terms.length) return "";
      const prevInTerms = prev && terms.some((t) => t.toLowerCase() === prev.toLowerCase());
      if (prevInTerms && hasHighlightMatch(previewBody, prev)) return prev;
      const matched = terms.find((t) => hasHighlightMatch(previewBody, t));
      return matched || terms[0];
    });
  }, [open, terms, previewBody, citationKey]);

  useEffect(() => {
    if (!open || !merged.document_id) {
      setViewMode("text");
      return;
    }
    if (!hasDocumentPreview) {
      setViewMode("text");
      return;
    }
    setViewMode("text");
  }, [open, merged.document_id, hasDocumentPreview, citationKey]);

  useEffect(() => {
    if (!open || !merged.document_id) {
      setFullText("");
      setFullTextMeta(null);
      setFullTextLoading(false);
      onError?.("");
      return;
    }

    const shouldLoadFullText = !hasDocumentPreview || viewMode === "text";
    if (!shouldLoadFullText) {
      setFullText("");
      setFullTextMeta(null);
      setFullTextLoading(false);
      onError?.("");
      return;
    }
    let cancelled = false;
    setFullText("");
    setFullTextMeta(null);
    setFullTextLoading(true);
    onError?.("");
    const query = showFullLargeText
      ? {
          full: true,
          maxChars: SERVER_WINDOW_REQUEST_CHARS,
        }
      : {
          maxChars: SERVER_WINDOW_REQUEST_CHARS,
          anchorId: fulltextAnchorId || undefined,
          highlight: fulltextHint || undefined,
          around: 0,
        };
    getDocumentFullText(merged.document_id, query)
      .then((res) => {
        if (cancelled) return;
        setFullText(String(res?.text || ""));
        setFullTextMeta(res && typeof res === "object" ? res : null);
        onError?.("");
      })
      .catch((e) => {
        if (cancelled) return;
        setFullText("");
        setFullTextMeta(null);
        onError?.(e?.message || "Не удалось загрузить полный текст документа");
      })
      .finally(() => {
        if (!cancelled) setFullTextLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, merged.document_id, onError, fulltextAnchorId, fulltextHint, citationKey, showFullLargeText, hasDocumentPreview, viewMode]);

  useEffect(() => {
    if (!open || showDocument || loading || fullTextLoading) return;
    const node = textPreviewRef.current;
    if (!node) return;
    const raf = window.requestAnimationFrame(() => {
      const target = node.querySelector("mark");
      if (target && typeof target.scrollIntoView === "function") {
        target.scrollIntoView({ behavior: "smooth", block: "center", inline: "nearest" });
      } else {
        node.scrollTop = 0;
      }
    });
    return () => window.cancelAnimationFrame(raf);
  }, [open, showDocument, loading, fullTextLoading, activeTerm, fullDocumentText, inlinePreview, citationKey]);

  if (!open) return null;

  return (
    <div className="source-viewer-backdrop" onClick={() => onClose?.()}>
      <section className="source-viewer-modal" role="dialog" aria-label="Просмотр документа" onClick={(e) => e.stopPropagation()}>
        <div className="source-viewer-head">
          <div className="source-viewer-title-wrap">
            <h3>Просмотр документа</h3>
            <div className="source-viewer-subtitle">
              {merged.document_id}/{merged.chunk_id}
              {typeof merged.chunk_index === "number" && merged.chunk_index >= 0 ? ` · фрагмент ${merged.chunk_index + 1}` : ""}
              {page ? ` · стр. ${page}` : ""}
              {locator.slide ? ` · слайд ${locator.slide}` : ""}
              {merged.section_path ? ` · ${merged.section_path}` : ""}
            </div>
          </div>
          <div className="source-viewer-actions">
            {sourceFileUrl && (
              <a className="secondary small" href={openOriginalUrl} target="_blank" rel="noopener noreferrer">
                Открыть оригинал
              </a>
            )}
            <button type="button" className="secondary small" onClick={() => onClose?.()}>
              Закрыть
            </button>
          </div>
        </div>

        <div className="source-viewer-layout source-viewer-layout-single">
          <div className="source-viewer-pane source-viewer-doc-pane">
            <div className="source-viewer-pane-head source-viewer-pane-head-row">
              <span>Документ ({sourceKindLabel(kind)})</span>
              {hasDocumentPreview && (
                <div className="source-viewer-view-mode" role="tablist" aria-label="Режим просмотра источника">
                  <button
                    type="button"
                    className={`source-viewer-view-btn ${showDocument ? "is-active" : ""}`}
                    onClick={() => setViewMode("document")}
                  >
                    Документ
                  </button>
                  <button
                    type="button"
                    className={`source-viewer-view-btn ${showDocument ? "" : "is-active"}`}
                    onClick={() => setViewMode("text")}
                  >
                    Текст
                  </button>
                </div>
              )}
            </div>
            {showDocument ? (
              <iframe className="source-viewer-iframe" src={viewerUrl} title="Оригинальный документ" />
            ) : fullTextLoading && !hasAnyPreviewText ? (
              <div className="source-viewer-loading">Загрузка полного текста…</div>
            ) : loading && !hasAnyPreviewText ? (
              <div className="source-viewer-loading">Загрузка фрагмента…</div>
            ) : fullDocumentText ? (
              <div className="source-viewer-text-preview" ref={textPreviewRef}>
                <div className="source-viewer-text-preview-note">
                  Показываем извлечённый текст документа и подсвечиваем нужный фрагмент.
                </div>
                {loading && (
                  <div className="source-viewer-warning-note">
                    Фрагмент ещё загружается; показываем полный текст документа.
                  </div>
                )}
                {canToggleWindow && (
                  <div className="source-viewer-window-actions">
                    <span className="source-viewer-window-note">
                      {effectiveWindowed
                        ? "Большой документ: показано окно вокруг найденного фрагмента."
                        : "Большой документ: показан полный текст."}
                    </span>
                    <button
                      type="button"
                      className="source-viewer-window-toggle"
                      onClick={() => setShowFullLargeText((v) => !v)}
                    >
                      {showFullLargeText ? "Показывать окно" : "Показать весь текст"}
                    </button>
                  </div>
                )}
                {effectiveWindowed && effectiveLeadingTruncated && (
                  <div className="source-viewer-window-ellipsis">… начало документа скрыто …</div>
                )}
                <pre className="source-viewer-text source-viewer-text-preview-body">
                  {renderHighlightedText(textWindow.text, activeTerm, textWindow.span)}
                </pre>
                {effectiveWindowed && effectiveTrailingTruncated && (
                  <div className="source-viewer-window-ellipsis">… конец документа скрыт …</div>
                )}
              </div>
            ) : inlinePreview ? (
              <div className="source-viewer-text-preview" ref={textPreviewRef}>
                <div className="source-viewer-text-preview-note">
                  Полный текст недоступен; показываем текстовый фрагмент.
                </div>
                <pre className="source-viewer-text source-viewer-text-preview-body">
                  {renderHighlightedText(textWindow.text, activeTerm, textWindow.span)}
                </pre>
              </div>
            ) : (
              <div className="source-viewer-placeholder">
                Просмотр в браузере недоступен.
                <br />
                Откройте оригинал файла или переключитесь на текстовый режим.
              </div>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}

import React, { useMemo, useState } from "react";
import "./SummaryPanel.css";

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
      out.push(`<li>${renderInline(ul[1])}</li>`);
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
      out.push(`<li>${renderInline(ol[1])}</li>`);
      continue;
    }
    closeLists();
    out.push(`<p>${renderInline(line)}</p>`);
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

  const openIdx = answer.toLowerCase().lastIndexOf("<think>");
  if (openIdx >= 0) {
    chunks.push(answer.slice(openIdx + "<think>".length).trim());
    answer = answer.slice(0, openIdx);
  }

  answer = answer.replace(/<\/think>/gi, "").trim();
  const reasoning = chunks.filter(Boolean).join("\n\n").trim();
  return { answer, reasoning };
}

function dedupeSources(rawSources) {
  const rows = Array.isArray(rawSources) ? rawSources : [];
  const out = [];
  const seen = new Set();
  for (const row of rows) {
    if (!row || typeof row !== "object") continue;
    const key = [
      String(row.document_id || "").trim(),
      String(row.chunk_id || "").trim(),
      String(row.anchor_id || "").trim(),
      String(row.evidence_id || "").trim(),
      String(row.chunk_index ?? "").trim(),
      String(row.text || "").replace(/\s+/g, " ").trim().slice(0, 120),
    ].join("|");
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(row);
  }
  return out;
}

export default function SummaryPanel({ summary, sources, isStreaming }) {
  const [showSources, setShowSources] = useState(false);
  const [copied, setCopied] = useState(false);
  const [viewMode, setViewMode] = useState("markdown");
  const { answer, reasoning } = splitReasoning(summary || "");
  const uniqueSources = useMemo(() => dedupeSources(sources), [sources]);
  const cleanAnswer = String(answer || "").trim();
  const answerChars = cleanAnswer.length;
  const sourceCount = uniqueSources.length;
  const hasSummaryContent = !!cleanAnswer;

  async function handleCopy() {
    if (!cleanAnswer) return;
    try {
      await navigator.clipboard.writeText(cleanAnswer);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  }

  return (
    <div className="card summary-panel">
      <div className="summary-panel-header">
        <div className="summary-head-main">
          <h3>Саммари {isStreaming && <span className="streaming-badge">Генерация…</span>}</h3>
          <div className="summary-meta-row">
            <span className="summary-meta-chip">Символов: {answerChars}</span>
            <span className="summary-meta-chip">Источников: {sourceCount}</span>
          </div>
        </div>
        {hasSummaryContent && (
          <div className="summary-head-actions">
            <div className="summary-view-switch" role="group" aria-label="Режим отображения саммари">
              <button
                type="button"
                className={`secondary small ${viewMode === "markdown" ? "is-active" : ""}`.trim()}
                onClick={() => setViewMode("markdown")}
              >
                Markdown
              </button>
              <button
                type="button"
                className={`secondary small ${viewMode === "plain" ? "is-active" : ""}`.trim()}
                onClick={() => setViewMode("plain")}
              >
                Текст
              </button>
            </div>
            <button
              type="button"
              className="secondary"
              onClick={handleCopy}
              title="Скопировать текст саммари в буфер обмена"
              disabled={!!isStreaming || !cleanAnswer}
            >
              {copied ? "Скопировано!" : "Копировать саммари"}
            </button>
          </div>
        )}
      </div>
      {!cleanAnswer ? (
        <div className={`summary-empty ${isStreaming ? "" : "is-compact"}`.trim()}>
          {isStreaming
            ? "Начинаю собирать саммари. Первые фрагменты появятся здесь."
            : "Саммари ещё не создано. Запустите шаг «2. Саммари»."}
        </div>
      ) : viewMode === "plain" ? (
        <pre className="summary-text summary-text-plain">{cleanAnswer}</pre>
      ) : (
        <div className="summary-text markdown-body" dangerouslySetInnerHTML={{ __html: markdownToHtml(cleanAnswer) }} />
      )}
      {reasoning && (
        <details className="summary-think">
          <summary>Рассуждение модели</summary>
          <div className="summary-think-body markdown-body" dangerouslySetInnerHTML={{ __html: markdownToHtml(reasoning) }} />
        </details>
      )}

      {sourceCount > 0 && (
        <>
          <button
            className="secondary toggle-sources"
            onClick={() => setShowSources(!showSources)}
            title="Фрагменты документа, на которых основано саммари"
          >
            {showSources ? "Скрыть источники" : `Показать источники (${sourceCount})`}
          </button>
          {showSources && (
            <ul className="source-list">
              {uniqueSources.map((s, i) => (
                <li key={i} className="source-item">
                  <span className="source-id">{s.chunk_id}</span>
                  <span className="source-text">{s.text}</span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}

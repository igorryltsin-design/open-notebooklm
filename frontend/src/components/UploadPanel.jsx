import React, { useRef, useState } from "react";
import { uploadFile, uploadUrl, importScriptOnly } from "../api/client";
import ConfirmDialog from "./ConfirmDialog";
import "./UploadPanel.css";

const SCRIPT_MODE = "script";

export default function UploadPanel({
  documentId,
  filename,
  compact = false,
  showDocumentListWhenActive = false,
  onUploaded,
  onError,
  onReset,
  onDeleteDocument,
  onBackToList,
  onScriptOnlyImported,
}) {
  const inputRef = useRef(null);
  const scriptInputRef = useRef(null);
  const [dragOver, setDragOver] = useState(false);
  const [loading, setLoading] = useState(false);
  const [urlMode, setUrlMode] = useState(false);
  const [urlValue, setUrlValue] = useState("");
  const [uploadMode, setUploadMode] = useState("file"); // "file" | "url" | "script"
  const [showResetConfirm, setShowResetConfirm] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

  async function handleFiles(files) {
    if (!files || files.length === 0) return;
    setLoading(true);
    try {
      const res = await uploadFile(files[0]);
      onUploaded(res.document_id, res.filename, res);
    } catch (e) {
      onError(e.message);
    } finally {
      setLoading(false);
    }
  }

  function isValidUrl(s) {
    const t = s.trim();
    if (!t) return false;
    try {
      const u = new URL(t);
      return u.protocol === "http:" || u.protocol === "https:";
    } catch {
      return false;
    }
  }

  async function handleUrl() {
    const trimmed = urlValue.trim();
    if (!trimmed) return;
    if (!isValidUrl(trimmed)) {
      onError("Введите корректный URL (например: https://example.com/статья)");
      return;
    }
    setLoading(true);
    try {
      const res = await uploadUrl(trimmed);
      onUploaded(res.document_id, res.filename, res);
    } catch (e) {
      onError(e.message);
    } finally {
      setLoading(false);
    }
  }

  async function handleScriptFile(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    setLoading(true);
    try {
      const text = await file.text();
      const data = JSON.parse(text);
      const raw = data.script ?? data;
      const list = Array.isArray(raw) ? raw : [];
      const script = [];
      for (const item of list) {
        if (item && typeof item === "object" && "text" in item) {
          script.push({ voice: item.voice || "Игорь", text: String(item.text) });
        }
      }
      if (script.length === 0) {
        onError("В JSON нужен массив script: [{ voice, text }, ...]");
        e.target.value = "";
        return;
      }
      const res = await importScriptOnly(script);
      if (onScriptOnlyImported) {
        onScriptOnlyImported({ document_id: res.document_id, filename: "Импорт скрипта", script: res.script });
      } else {
        onUploaded(res.document_id, "Импорт скрипта");
      }
    } catch (err) {
      onError(err.message || "Ошибка чтения JSON");
    } finally {
      setLoading(false);
      e.target.value = "";
    }
  }

  if (compact) {
    return (
      <div className="card upload-panel compact-upload-panel">
        <div
          className={`dropzone compact-dropzone ${dragOver ? "drag-over" : ""}`}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => { e.preventDefault(); setDragOver(false); handleFiles(e.dataTransfer.files); }}
          onClick={() => inputRef.current?.click()}
          title="Перетащите файл или нажмите для выбора"
        >
          <input
            ref={inputRef}
            type="file"
            accept=".pdf,.docx,.doc,.rtf,.odt,.otd,.ppt,.pptx,.djvu,.djv,.djvy,.txt,.md,.html"
            hidden
            onChange={(e) => handleFiles(e.target.files)}
          />
          {loading ? (
            <p>Загрузка…</p>
          ) : (
            <>
              <div className="drop-icon">+</div>
              <p className="text-muted">Файл</p>
            </>
          )}
        </div>
      </div>
    );
  }

  if (documentId) {
    return (
      <>
        <div className="card upload-done">
          <div>
            <span className="upload-label">Загружено:</span>{" "}
            <strong>{filename}</strong>{" "}
            <span className="text-muted">({documentId})</span>
          </div>
          <div className="upload-done-actions">
            {onBackToList && (
              <button
                className="secondary"
                onClick={onBackToList}
                title="Показать список документов, чтобы открыть другой"
              >
                {showDocumentListWhenActive ? "Скрыть список" : "Другие документы"}
              </button>
            )}
            <button
              className="secondary"
              onClick={() => setShowResetConfirm(true)}
              title="Сбросить активный документ и выбрать другой. Текущий файл уже сохранён в базе."
            >
              Сменить документ
            </button>
            {onDeleteDocument && (
              <button
                type="button"
                className="secondary upload-delete-btn"
                onClick={() => setShowDeleteConfirm(true)}
                title="Удалить документ из базы"
              >
                Удалить из базы
              </button>
            )}
          </div>
        </div>
        <ConfirmDialog
          open={showResetConfirm}
          title="Сменить активный документ"
          message="Файл уже сохранён в базе. Будет сброшен только текущий экран: открытый документ, саммари, скрипт и чат. Сам документ останется в списке, и его можно открыть снова в любой момент."
          cancelLabel="Отмена"
          confirmLabel="Сбросить и выбрать другой"
          onConfirm={() => {
            setShowResetConfirm(false);
            onReset();
          }}
          onCancel={() => setShowResetConfirm(false)}
        />
        <ConfirmDialog
          open={showDeleteConfirm}
          message="Удалить документ из базы? Файлы и индекс будут удалены."
          cancelLabel="Отмена"
          confirmLabel="Удалить"
          danger
          onConfirm={() => {
            setShowDeleteConfirm(false);
            onDeleteDocument?.();
          }}
          onCancel={() => setShowDeleteConfirm(false)}
        />
      </>
    );
  }

  const isFileMode = uploadMode === "file";
  const isUrlMode = uploadMode === "url";
  const isScriptMode = uploadMode === SCRIPT_MODE;

  return (
    <div className="card upload-panel">
      <div className="mode-toggle">
        <button
          className={!isFileMode ? "secondary" : ""}
          onClick={() => setUploadMode("file")}
          title="Загрузить документ с компьютера"
        >
          Загрузка файла
        </button>
        <button
          className={!isUrlMode ? "secondary" : ""}
          onClick={() => setUploadMode("url")}
          title="Указать ссылку на страницу или файл"
        >
          По URL
        </button>
        <button
          className={!isScriptMode ? "secondary" : ""}
          onClick={() => setUploadMode(SCRIPT_MODE)}
          title="Импорт скрипта подкаста из JSON без документа — сразу к генерации аудио/видео"
        >
          Импорт скрипта
        </button>
      </div>

      {isFileMode && (
        <div
          className={`dropzone ${dragOver ? "drag-over" : ""}`}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => { e.preventDefault(); setDragOver(false); handleFiles(e.dataTransfer.files); }}
          onClick={() => inputRef.current?.click()}
        >
          <input
            ref={inputRef}
            type="file"
            accept=".pdf,.docx,.doc,.rtf,.odt,.otd,.ppt,.pptx,.djvu,.djv,.djvy,.txt,.md,.html"
            hidden
            onChange={(e) => handleFiles(e.target.files)}
          />
          {loading ? (
            <p>Загрузка…</p>
          ) : (
            <>
              <div className="drop-icon">+</div>
              <p>Перетащите файл сюда или <strong>нажмите для выбора</strong></p>
              <p className="text-muted">PDF, DOCX, DOC, RTF, ODT, OTD, PPT, PPTX, DJVU, TXT, MD, HTML</p>
            </>
          )}
        </div>
      )}
      {isUrlMode && (
        <div className="url-input-row">
          <input
            type="url"
            placeholder="https://example.com/статья"
            value={urlValue}
            onChange={(e) => setUrlValue(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleUrl()}
          />
          <button onClick={handleUrl} disabled={loading || !urlValue.trim()} title="Загрузить по указанной ссылке">
            {loading ? "Загрузка…" : "Загрузить"}
          </button>
        </div>
      )}
      {isScriptMode && (
        <div
          className={`dropzone script-dropzone ${loading ? "loading" : ""}`}
          onClick={() => scriptInputRef.current?.click()}
        >
          <input
            ref={scriptInputRef}
            type="file"
            accept=".json"
            hidden
            onChange={handleScriptFile}
          />
          {loading ? (
            <p>Импорт…</p>
          ) : (
            <>
              <div className="drop-icon">📄</div>
              <p>Выберите JSON-файл скрипта</p>
              <p className="text-muted">Формат: {"{ \"script\": [ { \"voice\": \"Игорь\", \"text\": \"…\" }, … ] }"}</p>
              <p className="text-muted">После импорта можно сразу генерировать аудио и видео</p>
            </>
          )}
        </div>
      )}
    </div>
  );
}

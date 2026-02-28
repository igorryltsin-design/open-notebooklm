import React from "react";
import "./ConfirmDialog.css";

export default function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = "Подтвердить",
  cancelLabel = "Отмена",
  onConfirm,
  onCancel,
  danger = false,
}) {
  if (!open) return null;

  return (
    <div className="confirm-overlay" onClick={onCancel}>
      <div
        className={`confirm-dialog ${danger ? "confirm-dialog--danger" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label={title || confirmLabel}
        onClick={(e) => e.stopPropagation()}
      >
        {title && <h4 className="confirm-title">{title}</h4>}
        <p className="confirm-message">{message}</p>
        <div className="confirm-actions">
          <button type="button" className="secondary" onClick={onCancel}>
            {cancelLabel}
          </button>
          <button type="button" className={danger ? "accent" : ""} onClick={onConfirm}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
} from "react";
import "../styles/Toast.css";

/**
 * App-wide toast notifications and a promise-based confirm dialog.
 *
 * Both live here because both need to render above everything else and be
 * callable from any page:
 *
 *   const { toast, confirm } = useToast();
 *   toast.success("Customer saved");
 *   if (await confirm({ message: "Delete this zone?" })) { ... }
 *
 * confirm() returning a promise means a delete handler reads top to bottom
 * instead of splitting across callbacks - and unlike window.confirm it can be
 * styled, is keyboard accessible and does not block the JS thread.
 */

const ToastContext = createContext(null);

let nextId = 0;

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const [dialog, setDialog] = useState(null);
  const timers = useRef(new Map());

  const dismiss = useCallback((id) => {
    setToasts((list) => list.filter((t) => t.id !== id));
    const timer = timers.current.get(id);
    if (timer) {
      clearTimeout(timer);
      timers.current.delete(id);
    }
  }, []);

  const push = useCallback((tone, message, options = {}) => {
    const id = ++nextId;
    const duration = options.duration ?? (tone === "error" ? 7000 : 4000);

    setToasts((list) => {
      // Collapse an identical message rather than stacking duplicates - a
      // double-click on Save should not produce two identical toasts.
      const duplicate = list.find((t) => t.message === message && t.tone === tone);
      if (duplicate) return list;
      return [...list, { id, tone, message, title: options.title }];
    });

    if (duration > 0) {
      timers.current.set(id, setTimeout(() => dismiss(id), duration));
    }
    return id;
  }, [dismiss]);

  // Clear pending timers if the provider ever unmounts.
  useEffect(() => {
    const pending = timers.current;
    return () => {
      pending.forEach(clearTimeout);
      pending.clear();
    };
  }, []);

  const toast = useMemo(() => ({
    success: (message, options) => push("success", message, options),
    error: (message, options) => push("error", message, options),
    info: (message, options) => push("info", message, options),
    warning: (message, options) => push("warning", message, options),
    dismiss,
  }), [push, dismiss]);

  const confirm = useCallback((options) => new Promise((resolve) => {
    setDialog({
      title: "Are you sure?",
      message: "This action cannot be undone.",
      confirmLabel: "Confirm",
      cancelLabel: "Cancel",
      tone: "danger",
      ...(typeof options === "string" ? { message: options } : options),
      resolve,
    });
  }), []);

  const value = useMemo(() => ({ toast, confirm }), [toast, confirm]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <ToastViewport toasts={toasts} onDismiss={dismiss} />
      {dialog && (
        <ConfirmDialog
          {...dialog}
          onResolve={(answer) => {
            dialog.resolve(answer);
            setDialog(null);
          }}
        />
      )}
    </ToastContext.Provider>
  );
}

export function useToast() {
  const context = useContext(ToastContext);
  if (!context) throw new Error("useToast must be used inside ToastProvider");
  return context;
}

/* ------------------------------------------------------------------ */

const ICONS = { success: "✓", error: "!", warning: "!", info: "i" };

function ToastViewport({ toasts, onDismiss }) {
  if (!toasts.length) return null;

  return (
    <div className="toast-viewport" role="region" aria-label="Notifications">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`toast toast-${t.tone}`}
          role={t.tone === "error" ? "alert" : "status"}
          aria-live={t.tone === "error" ? "assertive" : "polite"}
        >
          <span className="toast-icon" aria-hidden="true">{ICONS[t.tone]}</span>
          <div className="toast-body">
            {t.title && <strong>{t.title}</strong>}
            <span>{t.message}</span>
          </div>
          <button
            type="button"
            className="toast-close"
            onClick={() => onDismiss(t.id)}
            aria-label="Dismiss notification"
          >
            ✕
          </button>
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */

function ConfirmDialog({ title, message, confirmLabel, cancelLabel, tone, onResolve }) {
  const confirmRef = useRef(null);

  useEffect(() => {
    confirmRef.current?.focus();

    function onKeyDown(event) {
      if (event.key === "Escape") onResolve(false);
      if (event.key === "Enter" && document.activeElement?.tagName !== "BUTTON") {
        onResolve(true);
      }
    }
    document.addEventListener("keydown", onKeyDown);

    // Stop the page behind the dialog from scrolling.
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [onResolve]);

  return (
    <div className="confirm-scrim">
      <div
        className="confirm-card"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        aria-describedby="confirm-message"
        onClick={(event) => event.stopPropagation()}
      >
        <h2 id="confirm-title">{title}</h2>
        <p id="confirm-message">{message}</p>
        <div className="confirm-actions">
          <button type="button" className="btn" onClick={() => onResolve(false)}>
            {cancelLabel}
          </button>
          <button
            ref={confirmRef}
            type="button"
            className={`btn ${tone === "danger" ? "danger" : "primary"}`}
            onClick={() => onResolve(true)}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

export default ToastProvider;

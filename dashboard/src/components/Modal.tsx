// A dialog over the page: closes with Esc, the × button or a click outside; keeps keyboard focus inside.
import { useEffect, useId, useRef, type ReactNode } from "react";

interface ModalProps {
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  size?: "sm" | "md" | "xl";
  /** Dark full-screen look for showing a picture. */
  variant?: "default" | "image";
  /** While saving, Esc and outside clicks do nothing. */
  busy?: boolean;
}

const widths = { sm: "max-w-md", md: "max-w-lg", xl: "max-w-6xl" };

export function Modal({ title, onClose, children, footer, size = "md", variant = "default", busy = false }: ModalProps) {
  const titleId = useId();
  const panel = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const busyRef = useRef(busy);
  busyRef.current = busy;

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    // Focus the first field (or the dialog itself) unless something inside already asked for focus.
    if (!panel.current?.contains(document.activeElement)) {
      const first = panel.current?.querySelector<HTMLElement>("input, select, textarea, [data-autofocus]");
      (first ?? panel.current)?.focus();
    }

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busyRef.current) {
        e.stopPropagation();
        closeRef.current();
      }
      if (e.key === "Tab" && panel.current) {
        const items = [...panel.current.querySelectorAll<HTMLElement>(
          "a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled])",
        )];
        if (items.length === 0) return;
        const first = items[0];
        const last = items[items.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = overflow;
      previous?.focus?.();
    };
  }, []);

  const image = variant === "image";
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 sm:p-6">
      <div className={`absolute inset-0 ${image ? "bg-slate-950/90" : "bg-slate-900/50"}`} aria-hidden="true"
        onClick={() => !busy && onClose()} />
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={`relative flex max-h-full w-full ${widths[size]} flex-col outline-none ${
          image ? "" : "overflow-hidden rounded-xl bg-white shadow-xl"}`}
      >
        <header className={`flex items-center justify-between gap-3 ${
          image ? "pb-3 text-white" : "border-b border-slate-100 px-5 py-4"}`}>
          <h2 id={titleId} className={`text-base font-semibold ${image ? "text-white" : "text-slate-900"}`}>{title}</h2>
          <button type="button" aria-label="Close" disabled={busy} onClick={onClose}
            className={`rounded-lg p-1.5 ${image ? "text-slate-300 hover:bg-white/10 hover:text-white"
              : "text-slate-400 hover:bg-slate-100 hover:text-slate-600"}`}>
            <svg viewBox="0 0 20 20" className="h-5 w-5" fill="currentColor" aria-hidden="true">
              <path d="M5.3 5.3a1 1 0 0 1 1.4 0L10 8.6l3.3-3.3a1 1 0 1 1 1.4 1.4L11.4 10l3.3 3.3a1 1 0 0 1-1.4 1.4L10 11.4l-3.3 3.3a1 1 0 0 1-1.4-1.4L8.6 10 5.3 6.7a1 1 0 0 1 0-1.4Z" />
            </svg>
          </button>
        </header>
        <div className={image ? "min-h-0 flex-1" : "min-h-0 flex-1 overflow-y-auto px-5 py-4"}>{children}</div>
        {footer && (
          <footer className="flex flex-wrap justify-end gap-2 border-t border-slate-100 bg-slate-50 px-5 py-3">
            {footer}
          </footer>
        )}
      </div>
    </div>
  );
}

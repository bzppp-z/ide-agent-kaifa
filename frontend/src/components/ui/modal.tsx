import type { ReactNode } from "react";
import { useEffect, useRef } from "react";

import { cn } from "../../lib/cn";

interface ModalProps {
  title: string;
  subtitle?: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  /** 嵌套弹窗时可由外层禁用，避免 Esc 一次关闭两层。 */
  closeOnEsc?: boolean;
  /** 覆盖默认宽度（如 max-w-lg）。 */
  widthClassName?: string;
}

export function Modal({
  title,
  subtitle,
  onClose,
  children,
  footer,
  closeOnEsc = true,
  widthClassName = "max-w-lg",
}: ModalProps) {
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    if (!closeOnEsc) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onCloseRef.current();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [closeOnEsc]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={cn(
          "flex max-h-[calc(100vh-2rem)] w-full flex-col overflow-hidden rounded-lg border border-surface-border bg-panel shadow-2xl",
          widthClassName,
        )}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="flex shrink-0 items-start justify-between gap-3 border-b border-surface-border px-4 py-3">
          <div className="min-w-0">
            <h2 className="text-[14px] font-semibold text-text">{title}</h2>
            {subtitle ? (
              <p className="mt-0.5 text-[11px] text-muted-dim">{subtitle}</p>
            ) : null}
          </div>
          <button
            type="button"
            title="关闭（Esc）"
            onClick={onClose}
            className="flex h-6 w-6 shrink-0 items-center justify-center rounded text-[16px] leading-none text-muted-dim hover:bg-surface-alt hover:text-text"
          >
            ×
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">{children}</div>

        {footer ? (
          <footer className="flex shrink-0 items-center justify-end gap-2 border-t border-surface-border px-4 py-3">
            {footer}
          </footer>
        ) : null}
      </div>
    </div>
  );
}

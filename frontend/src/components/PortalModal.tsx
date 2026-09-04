import { useEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

interface PortalModalProps {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  className?: string;
  style?: React.CSSProperties;
  testId?: string;
  ariaLabel?: string;
}

/** Modal rendered through a portal to <body>, so it is immune to any
 *  transform/filter on ancestor containers (e.g. the route transition) and
 *  always positions against the viewport. Closes on backdrop click + Escape,
 *  and locks body scroll while open. */
export function PortalModal({ open, onClose, children, className = "", style, testId, ariaLabel = "对话框" }: PortalModalProps) {
  const dialogRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const selector = 'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
    const dialog = dialogRef.current;
    (dialog?.querySelector<HTMLElement>(selector) || dialog)?.focus({ preventScroll: true });
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
        return;
      }
      if (e.key !== "Tab" || !dialog) return;
      const focusable = Array.from(dialog.querySelectorAll<HTMLElement>(selector));
      if (!focusable.length) {
        e.preventDefault();
        dialog.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
      previouslyFocused?.focus({ preventScroll: true });
    };
  }, [open, onClose]);

  if (!open) return null;

  return createPortal(
    <div className="modal-backdrop" onClick={onClose} data-testid={testId}>
      <div
        ref={dialogRef}
        className={"modal" + (className ? " " + className : "")}
        onClick={(e) => e.stopPropagation()}
        style={style}
        role="dialog"
        aria-modal="true"
        aria-label={ariaLabel}
        tabIndex={-1}
      >
        {children}
      </div>
    </div>,
    document.body,
  );
}

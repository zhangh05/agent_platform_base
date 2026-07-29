import { useEffect, type ReactNode } from "react";
import { createPortal } from "react-dom";

interface PortalModalProps {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  className?: string;
  style?: React.CSSProperties;
  testId?: string;
}

/** Modal rendered through a portal to <body>, so it is immune to any
 *  transform/filter on ancestor containers (e.g. the route transition) and
 *  always positions against the viewport. Closes on backdrop click + Escape,
 *  and locks body scroll while open. */
export function PortalModal({ open, onClose, children, className = "", style, testId }: PortalModalProps) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [open, onClose]);

  if (!open) return null;

  return createPortal(
    <div className="modal-backdrop" onClick={onClose} data-testid={testId}>
      <div
        className={"modal" + (className ? " " + className : "")}
        onClick={(e) => e.stopPropagation()}
        style={style}
        role="dialog"
        aria-modal="true"
      >
        {children}
      </div>
    </div>,
    document.body,
  );
}

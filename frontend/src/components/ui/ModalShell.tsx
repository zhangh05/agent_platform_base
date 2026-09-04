import { useEffect, useId, useRef, type ReactNode } from "react";
import { Button } from "./Button";
import { IconClose } from "../Icon";

interface ModalShellProps {
  open: boolean;
  onClose?: () => void;
  title?: ReactNode;
  subtitle?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  size?: "default" | "sheet";
  className?: string;
}

export function ModalShell({
  open,
  onClose,
  title,
  subtitle,
  children,
  footer,
  size = "default",
  className = "",
}: ModalShellProps) {
  const titleId = useId();
  const subtitleId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const dialog = dialogRef.current;
    const focusableSelector = 'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
    const first = dialog?.querySelector<HTMLElement>(focusableSelector);
    (first || dialog)?.focus({ preventScroll: true });

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && onClose) {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab" || !dialog) return;
      const focusable = Array.from(dialog.querySelectorAll<HTMLElement>(focusableSelector));
      if (focusable.length === 0) {
        event.preventDefault();
        dialog.focus();
        return;
      }
      const firstItem = focusable[0];
      const lastItem = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === firstItem) {
        event.preventDefault();
        lastItem.focus();
      } else if (!event.shiftKey && document.activeElement === lastItem) {
        event.preventDefault();
        firstItem.focus();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      previouslyFocused?.focus({ preventScroll: true });
    };
  }, [onClose, open]);

  if (!open) return null;

  const handleBackdropClick = onClose ? onClose : undefined;

  if (size === "sheet") {
    return (
      <div className="modal-overlay" onClick={handleBackdropClick}>
        <div ref={dialogRef} className={`modal-sheet ${className}`.trim()} onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-labelledby={title ? titleId : undefined} aria-describedby={subtitle ? subtitleId : undefined} tabIndex={-1}>
          {(title || onClose) && (
            <div className="modal-header">
              <div>
                {title && <div className="modal-title" id={titleId}>{title}</div>}
                {subtitle && <div className="modal-subtitle" id={subtitleId}>{subtitle}</div>}
              </div>
              {onClose && (
                <Button variant="ghost" size="sm" className="modal-close" onClick={onClose} aria-label="关闭对话框">
                  <IconClose size={16} aria-hidden="true" />
                </Button>
              )}
            </div>
          )}
          {children}
          {footer && <div className="modal-footer">{footer}</div>}
        </div>
      </div>
    );
  }

  return (
    <div className="modal-backdrop" onClick={handleBackdropClick}>
      <div ref={dialogRef} className={`modal ${className}`.trim()} onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-labelledby={title ? titleId : undefined} aria-describedby={subtitle ? subtitleId : undefined} tabIndex={-1}>
        {title && <div className="modal-title" id={titleId}>{title}</div>}
        {subtitle && <div className="modal-subtitle" id={subtitleId}>{subtitle}</div>}
        {children}
        {footer && <div className="modal-actions">{footer}</div>}
      </div>
    </div>
  );
}

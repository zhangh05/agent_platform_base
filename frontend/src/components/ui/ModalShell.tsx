import type { ReactNode } from "react";
import { Button } from "./Button";

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
  if (!open) return null;

  const handleBackdropClick = onClose ? onClose : undefined;

  if (size === "sheet") {
    return (
      <div className="modal-overlay" onClick={handleBackdropClick} role="dialog" aria-modal="true">
        <div className={`modal-sheet ${className}`.trim()} onClick={(e) => e.stopPropagation()}>
          {(title || onClose) && (
            <div className="modal-header">
              <div>
                {title && <div className="modal-title">{title}</div>}
                {subtitle && <div className="modal-subtitle">{subtitle}</div>}
              </div>
              {onClose && (
                <Button variant="ghost" size="sm" className="modal-close" onClick={onClose}>
                  ×
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
    <div className="modal-backdrop" onClick={handleBackdropClick} role="dialog" aria-modal="true">
      <div className={`modal ${className}`.trim()} onClick={(e) => e.stopPropagation()}>
        {title && <div className="modal-title">{title}</div>}
        {children}
        {footer && <div className="modal-actions">{footer}</div>}
      </div>
    </div>
  );
}

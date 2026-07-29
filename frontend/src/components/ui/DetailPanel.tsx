import type { ReactNode } from "react";
import { EmptyState } from "../common";

interface DetailPanelProps {
  title?: ReactNode;
  subtitle?: ReactNode;
  children?: ReactNode;
  actions?: ReactNode;
  onClose?: () => void;
  empty?: { text: string; hint?: string };
  className?: string;
}

export function DetailPanel({
  title,
  subtitle,
  children,
  actions,
  onClose,
  empty,
  className = "",
}: DetailPanelProps) {
  if (!children && empty) {
    return (
      <div className={`split-detail ui-detail-empty ${className}`}>
        <EmptyState text={empty.text} hint={empty.hint} />
      </div>
    );
  }

  return (
    <div className={`split-detail ui-detail-panel ${className}`}>
      {(title || actions || onClose) && (
        <div className="ui-detail-panel-head">
          <div>
            {title && <h3 className="ui-detail-panel-title">{title}</h3>}
            {subtitle && <div className="ui-detail-panel-subtitle">{subtitle}</div>}
          </div>
          <div className="ui-detail-panel-actions">
            {actions}
            {onClose && (
              <button className="btn sm ghost" onClick={onClose} type="button">
                ×
              </button>
            )}
          </div>
        </div>
      )}
      <div className="ui-detail-panel-body">{children}</div>
    </div>
  );
}

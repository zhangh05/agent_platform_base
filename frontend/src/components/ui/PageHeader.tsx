import type { ReactNode } from "react";

interface PageHeaderProps {
  title: ReactNode;
  subtitle?: ReactNode;
  children?: ReactNode;
  className?: string;
}

export function PageHeader({ title, subtitle, children, className = "" }: PageHeaderProps) {
  return (
    <div className={`page-header ui-page-header ${className}`}>
      <div>
        <h1>{title}</h1>
        {subtitle && <p className="subtitle">{subtitle}</p>}
      </div>
      {children && <div className="row-flex ui-page-header-actions">{children}</div>}
    </div>
  );
}

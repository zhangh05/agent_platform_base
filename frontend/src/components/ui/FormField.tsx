import type { ReactNode } from "react";

interface FormFieldProps {
  label?: ReactNode;
  htmlFor?: string;
  hint?: ReactNode;
  error?: ReactNode;
  children: ReactNode;
  className?: string;
  required?: boolean;
}

export function FormField({
  label,
  htmlFor,
  hint,
  error,
  children,
  className = "",
  required,
}: FormFieldProps) {
  return (
    <div className={`ui-form-field ${className}`.trim()}>
      {label && (
        <label className="ui-form-label" htmlFor={htmlFor}>
          {label}
          {required && <span className="ui-form-required"> *</span>}
        </label>
      )}
      {hint && <div className="ui-form-hint">{hint}</div>}
      <div className="ui-form-control">{children}</div>
      {error && <div className="ui-form-error">{error}</div>}
    </div>
  );
}

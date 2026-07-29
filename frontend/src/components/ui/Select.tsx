import type { SelectHTMLAttributes, ReactNode } from "react";

interface SelectProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, "className"> {
  className?: string;
  children: ReactNode;
}

export function Select({ className = "", children, ...rest }: SelectProps) {
  return (
    <select className={`input ${className}`.trim()} {...rest}>
      {children}
    </select>
  );
}

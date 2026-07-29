import type { ButtonHTMLAttributes, ReactNode } from "react";

type ButtonVariant = "default" | "primary" | "ghost" | "danger" | "danger-ghost";
type ButtonSize = "default" | "sm";

interface ButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "className"> {
  children?: ReactNode;
  variant?: ButtonVariant;
  size?: ButtonSize;
  icon?: ReactNode;
  iconOnly?: boolean;
  className?: string;
}

export function Button({
  children,
  variant = "default",
  size = "default",
  icon,
  iconOnly,
  className = "",
  ...rest
}: ButtonProps) {
  const variantClass = variant === "default" ? "" : variant;
  const sizeClass = size === "default" ? "" : size;
  const iconOnlyClass = iconOnly ? "icon-only" : "";

  return (
    <button
      className={`btn ${variantClass} ${sizeClass} ${iconOnlyClass} ${className}`.trim()}
      type={rest.type ?? "button"}
      {...rest}
    >
      {icon}
      {children}
    </button>
  );
}

import type { InputHTMLAttributes } from "react";

interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "className"> {
  className?: string;
}

export function Input({ className = "", ...rest }: InputProps) {
  return <input className={`input ${className}`.trim()} {...rest} />;
}

import type { TextareaHTMLAttributes } from "react";

interface TextareaProps extends Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "className"> {
  className?: string;
}

export function Textarea({ className = "", ...rest }: TextareaProps) {
  return <textarea className={`input ${className}`.trim()} {...rest} />;
}

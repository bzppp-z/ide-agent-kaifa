import type { ButtonHTMLAttributes } from "react";

import { cn } from "../../lib/cn";

type ButtonVariant = "brand" | "outline" | "ghost" | "danger";
type ButtonSize = "sm" | "md" | "icon";

const VARIANTS: Record<ButtonVariant, string> = {
  brand: "bg-brand text-[#05231A] hover:bg-brand-hover",
  outline:
    "border border-surface-border bg-surface-alt text-muted hover:border-[#2E343A] hover:text-text",
  ghost: "text-muted hover:bg-surface-alt hover:text-text",
  danger:
    "border border-red-500/30 bg-red-500/10 text-red-400 hover:border-red-500/50 hover:bg-red-500/15",
};

const SIZES: Record<ButtonSize, string> = {
  sm: "h-7 px-2.5 text-[12px]",
  md: "h-8 px-3 text-[12.5px]",
  icon: "h-7 w-7 p-0",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
}

export function Button({
  variant = "outline",
  size = "md",
  className,
  type = "button",
  ...props
}: ButtonProps) {
  return (
    <button
      type={type}
      className={cn(
        "inline-flex select-none items-center justify-center gap-1.5 rounded font-medium transition-colors",
        "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-brand/40",
        "disabled:pointer-events-none disabled:opacity-40",
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      {...props}
    />
  );
}

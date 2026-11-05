import type { HTMLAttributes } from "react";

import { cn } from "../../lib/cn";

type BadgeTone = "brand" | "danger" | "neutral" | "warning";

const TONES: Record<BadgeTone, string> = {
  brand: "border border-brand/30 bg-brand/10 text-brand",
  danger: "border border-red-500/30 bg-red-500/10 text-red-400",
  neutral: "border border-surface-border bg-surface text-muted",
  warning: "border border-yellow-500/30 bg-yellow-500/10 text-yellow-400",
};

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone;
}

export function Badge({ tone = "neutral", className, ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded px-1.5 py-0.5 font-mono text-[10.5px] leading-none",
        TONES[tone],
        className,
      )}
      {...props}
    />
  );
}

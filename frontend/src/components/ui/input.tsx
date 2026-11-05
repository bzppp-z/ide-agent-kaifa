import type { InputHTMLAttributes } from "react";

import { cn } from "../../lib/cn";

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        "h-8 w-full rounded border border-surface-border bg-surface-alt px-2.5 text-[12.5px] text-text",
        "placeholder:text-muted-dim focus:border-brand/40 focus:outline-none",
        className,
      )}
      {...props}
    />
  );
}

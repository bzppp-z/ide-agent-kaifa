import { cn } from "../../lib/cn";

export function Spinner({
  size = 12,
  className,
}: {
  size?: number;
  className?: string;
}) {
  return (
    <span
      style={{ width: size, height: size }}
      className={cn(
        "inline-block shrink-0 animate-spin rounded-full border border-surface-border border-t-muted",
        className,
      )}
      role="status"
      aria-label="loading"
    />
  );
}

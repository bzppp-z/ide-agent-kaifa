import { useSessionStore } from "../stores/useSessionStore";
import { Spinner } from "./ui/spinner";

const LABELS = {
  green: "沙箱就绪",
  yellow: "沙箱启动中…",
  red: "沙箱异常",
} as const;

export function SandboxStatus({ onRetry }: { onRetry: () => void }) {
  const status = useSessionStore((state) => state.containerStatus);
  const error = useSessionStore((state) => state.error);

  return (
    <div className="flex items-center gap-2 text-[11.5px]" title={error ?? undefined}>
      {status === "yellow" ? (
        <Spinner size={9} />
      ) : (
        <span
          className={
            status === "green"
              ? "h-1.5 w-1.5 rounded-full bg-brand"
              : "h-1.5 w-1.5 rounded-full bg-red-500"
          }
        />
      )}
      <span
        className={
          status === "green"
            ? "text-muted"
            : status === "yellow"
              ? "text-yellow-400/90"
              : "text-red-400"
        }
      >
        {LABELS[status]}
      </span>
      {status === "red" ? (
        <button
          type="button"
          onClick={onRetry}
          className="rounded border border-surface-border px-1.5 py-0.5 text-[10.5px] text-muted transition-colors hover:border-[#2E343A] hover:text-text"
        >
          重试
        </button>
      ) : null}
    </div>
  );
}

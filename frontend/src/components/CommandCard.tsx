import { useState } from "react";

import { formatError, respondCommand } from "../lib/api";
import { cn } from "../lib/cn";
import type { ChatCommandItem, ChatCommandStatus } from "../stores/useChatStore";
import { useChatStore } from "../stores/useChatStore";
import { Button } from "./ui/button";

const STATUS_LABEL: Record<ChatCommandStatus, string> = {
  pending: "待审批",
  running: "运行中",
  success: "成功",
  failed: "失败",
  rejected: "已拒绝",
};

function statusTone(status: ChatCommandStatus): string {
  if (status === "success") return "text-brand";
  if (status === "failed" || status === "rejected") return "text-red-400/90";
  return "text-muted-dim";
}

/** agent 命令卡片：审批按钮 + 状态 + 流式输出。 */
export function CommandCard({ item }: { item: ChatCommandItem }) {
  const setCommandStatus = useChatStore((state) => state.setCommandStatus);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const decide = async (accepted: boolean) => {
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await respondCommand(item.commandId, accepted);
      setCommandStatus(item.commandId, accepted ? "running" : "rejected");
    } catch (cause) {
      setError(formatError(cause));
    } finally {
      setSubmitting(false);
    }
  };

  const showOutput = item.status === "running" || item.output.length > 0;

  return (
    <div
      className={cn(
        "rounded border border-surface-border border-l-2 bg-surface-alt px-2.5 py-2",
        item.status === "failed" || item.status === "rejected"
          ? "border-l-red-500"
          : "border-l-brand/60",
      )}
    >
      <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted">
        <span>命令</span>
        <code className="break-all font-mono text-[11.5px] text-text">
          {item.command}
        </code>
        <span className={cn("ml-auto", statusTone(item.status))}>
          {STATUS_LABEL[item.status]}
        </span>
      </div>

      {item.reason ? (
        <div className="mt-1 text-[10.5px] leading-relaxed text-muted-dim">
          原因：{item.reason}
        </div>
      ) : null}

      <div className="mt-1 flex flex-wrap items-center gap-3 font-mono text-[10px] text-muted-dim">
        <span>超时 {item.timeoutSeconds}s</span>
        {item.exitCode !== null ? <span>exit {item.exitCode}</span> : null}
        {item.durationMs !== null ? <span>{item.durationMs}ms</span> : null}
      </div>

      {item.status === "pending" ? (
        <div className="mt-1.5 flex items-center gap-2">
          <Button
            variant="brand"
            size="sm"
            disabled={submitting}
            onClick={() => void decide(true)}
          >
            执行
          </Button>
          <Button
            variant="danger"
            size="sm"
            disabled={submitting}
            onClick={() => void decide(false)}
          >
            拒绝
          </Button>
          {submitting ? (
            <span className="text-[10.5px] text-muted-dim">提交中…</span>
          ) : null}
        </div>
      ) : null}

      {showOutput ? (
        <pre className="mt-1.5 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded border border-surface-border bg-bg px-2.5 py-1.5 font-mono text-[11px] leading-relaxed text-muted">
          {item.output || "运行中…"}
        </pre>
      ) : null}

      {error ? (
        <div className="mt-1 text-[10.5px] text-red-400/90">{error}</div>
      ) : null}
    </div>
  );
}

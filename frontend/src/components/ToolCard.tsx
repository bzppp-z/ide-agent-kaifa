import { useState } from "react";

import { cn } from "../lib/cn";
import type { ChatToolItem } from "../stores/useChatStore";
import { Badge } from "./ui/badge";
import { ChevronRight } from "./ui/icons";
import { Spinner } from "./ui/spinner";

const SUMMARY_LIMIT = 200;

function formatResult(result: unknown): string {
  if (result === undefined || result === null) return "";
  if (typeof result === "string") return result;
  try {
    return JSON.stringify(result, null, 2);
  } catch {
    return String(result);
  }
}

export function ToolCard({ item }: { item: ChatToolItem }) {
  const [argsOpen, setArgsOpen] = useState(false);
  const [resultExpanded, setResultExpanded] = useState(false);

  const calling = item.state === "calling";
  const ok = item.state === "ok";
  const resultText = formatResult(item.result);
  const isLong = resultText.length > SUMMARY_LIMIT;
  const visibleResult =
    isLong && !resultExpanded
      ? `${resultText.slice(0, SUMMARY_LIMIT)}…`
      : resultText;

  return (
    <div
      className={cn(
        "overflow-hidden rounded border border-surface-border border-l-2 bg-surface-alt",
        ok || calling ? "border-l-brand" : "border-l-red-500",
      )}
    >
      <div className="flex items-center gap-2 px-2.5 py-1.5">
        {calling ? (
          <Spinner size={10} />
        ) : (
          <Badge tone={ok ? "brand" : "danger"}>{ok ? "✓" : "✗"}</Badge>
        )}
        <span className="font-mono text-[12px] text-text">
          工具 · {item.toolName}
        </span>
        <span className="text-[10.5px] text-muted-dim">
          {calling ? "调用中" : ok ? "成功" : "失败"}
        </span>
        <span className="ml-auto font-mono text-[10.5px] text-muted-dim">
          {item.durationMs !== undefined ? `${item.durationMs}ms` : ""}
        </span>
      </div>

      <div className="space-y-1.5 px-2.5 pb-2">
        <button
          type="button"
          onClick={() => setArgsOpen((value) => !value)}
          className="flex items-center gap-1 text-[11px] text-muted transition-colors hover:text-text"
        >
          <ChevronRight open={argsOpen} className="h-3 w-3" />
          参数
        </button>
        {argsOpen ? (
          <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded bg-bg px-2 py-1.5 font-mono text-[11px] leading-relaxed text-muted">
            {JSON.stringify(item.args ?? {}, null, 2)}
          </pre>
        ) : null}

        {!calling && resultText ? (
          <div className="space-y-1">
            <span className="block text-[11px] text-muted-dim">结果</span>
            <pre
              className={cn(
                "max-h-48 overflow-auto whitespace-pre-wrap break-words rounded bg-bg px-2 py-1.5 font-mono text-[11px] leading-relaxed",
                ok ? "text-muted" : "text-red-400/90",
              )}
            >
              {visibleResult}
            </pre>
            {isLong ? (
              <button
                type="button"
                onClick={() => setResultExpanded((value) => !value)}
                className="text-[10.5px] text-muted-dim transition-colors hover:text-text"
              >
                {resultExpanded ? "收起" : "展开全部"}
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}

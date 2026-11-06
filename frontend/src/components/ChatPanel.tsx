import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";

import type { ChatContextItem, ChatItem } from "../stores/useChatStore";
import { useChatStore } from "../stores/useChatStore";
import { useSessionStore } from "../stores/useSessionStore";
import { ChangeList } from "./ChangeList";
import { ChangeSetCard } from "./ChangeSetCard";
import { CommandCard } from "./CommandCard";
import { PatchDiff } from "./PatchDiff";
import { PlanCard } from "./PlanCard";
import { SseMessage } from "./SseMessage";
import { ToolCard } from "./ToolCard";
import { Button } from "./ui/button";
import { SendIcon } from "./ui/icons";

function ContextNotice({ item }: { item: ChatContextItem }) {
  const [expanded, setExpanded] = useState(false);
  const hasSummary = item.summary.trim().length > 0;
  const label = hasSummary
    ? "已压缩早期上下文（保留目标与未完成项）"
    : item.warning ?? "压缩失败，已截断早期消息";

  return (
    <div className="rounded border border-surface-border bg-surface-alt px-2.5 py-1.5">
      <button
        type="button"
        onClick={() => hasSummary && setExpanded((value) => !value)}
        disabled={!hasSummary}
        className={`flex w-full items-center gap-1.5 text-left text-[10.5px] ${
          hasSummary ? "text-muted-dim" : "text-yellow-400/90"
        }`}
      >
        <span className="font-mono">context</span>
        <span>{label}</span>
        {hasSummary ? (
          <span className="ml-auto text-muted-dim">
            {expanded ? "收起" : "展开"}
          </span>
        ) : null}
      </button>
      {expanded && hasSummary ? (
        <div className="mt-1.5 whitespace-pre-wrap break-words border-t border-surface-border pt-1.5 text-[11px] leading-relaxed text-muted">
          {item.summary}
        </div>
      ) : null}
    </div>
  );
}

function ChatEventView({
  item,
  streaming,
}: {
  item: ChatItem;
  streaming: boolean;
}) {
  switch (item.kind) {
    case "user":
      return (
        <div className="flex justify-end">
          <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-lg rounded-br-sm border border-brand/25 bg-brand/10 px-3 py-2 text-[13px] leading-relaxed text-text">
            {item.content}
          </div>
        </div>
      );

    case "assistant":
      return (
        <div className="flex justify-start">
          <div className="max-w-[92%] rounded-lg rounded-bl-sm border border-surface-border bg-surface-alt px-3 py-2">
            <SseMessage
              content={item.content}
              streaming={streaming && item.streaming}
            />
          </div>
        </div>
      );

    case "tool":
      return <ToolCard item={item} />;

    case "patch":
      return <PatchDiff item={item} />;

    case "change_set":
      return <ChangeSetCard item={item} />;

    case "command":
      return <CommandCard item={item} />;

    case "context":
      return <ContextNotice item={item} />;

    case "output":
      return (
        <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded border border-surface-border bg-bg px-2.5 py-1.5 font-mono text-[11px] leading-relaxed text-muted">
          {item.chunk}
        </pre>
      );

    case "error":
      return (
        <div className="rounded border border-red-500/25 bg-red-500/5 px-2.5 py-1.5 text-[11.5px] leading-relaxed text-red-400/90">
          {item.message}
          {item.code ? (
            <span className="ml-1 font-mono text-[10px] text-red-400/60">
              [{item.code}]
            </span>
          ) : null}
        </div>
      );

    case "done":
      return (
        <div className="space-y-1">
          <div className="flex items-center gap-2 text-[10.5px] text-muted-dim">
            <span className="h-px flex-1 bg-surface-border" />
            <span className="font-mono">
              {item.stopped ? "已停止" : "完成"} · {item.totalSteps} 步 ·{" "}
              {item.totalTokens} tokens
            </span>
            <span className="h-px flex-1 bg-surface-border" />
          </div>
          {item.warning ? (
            <div className="text-[10.5px] text-yellow-400/90">{item.warning}</div>
          ) : null}
        </div>
      );

    default:
      return null;
  }
}

export function ChatPanel() {
  const items = useChatStore((state) => state.items);
  const plan = useChatStore((state) => state.plan);
  const changes = useChatStore((state) => state.changes);
  const loadChanges = useChatStore((state) => state.loadChanges);
  const streaming = useChatStore((state) => state.streaming);
  const sendMessage = useChatStore((state) => state.sendMessage);
  const stopStreaming = useChatStore((state) => state.stopStreaming);
  const sessionId = useSessionStore((state) => state.id);
  const containerStatus = useSessionStore((state) => state.containerStatus);

  const [draft, setDraft] = useState("");
  const [changesOpen, setChangesOpen] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const element = listRef.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [items, streaming]);

  // run 结束后刷新变更清单（落盘/失败状态以数据库为准）
  useEffect(() => {
    if (sessionId && !streaming) void loadChanges(sessionId);
  }, [sessionId, streaming, loadChanges]);

  const lastDone = [...items]
    .reverse()
    .find((item) => item.kind === "done");
  const stats =
    lastDone && lastDone.kind === "done"
      ? `${lastDone.totalSteps} 步 · ${lastDone.totalTokens} tokens`
      : null;

  const canSend =
    Boolean(sessionId) &&
    containerStatus !== "red" &&
    draft.trim().length > 0 &&
    !streaming;

  const submit = () => {
    if (!canSend || !sessionId) return;
    void sendMessage(sessionId, draft);
    setDraft("");
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <aside className="flex w-[380px] shrink-0 flex-col border-l border-surface-border bg-panel">
      <header className="flex h-9 shrink-0 items-center gap-2 border-b border-surface-border px-3">
        <span className="text-[10.5px] uppercase tracking-wider text-muted-dim">
          对话
        </span>
        {streaming ? (
          <span className="ml-auto flex items-center gap-1.5 text-[10.5px] text-muted">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-brand" />
            流式输出中
          </span>
        ) : (
          <span className="ml-auto text-[10.5px] text-muted-dim">agent</span>
        )}
      </header>

      {plan && plan.items.length > 0 ? (
        <div className="shrink-0 px-3 pt-3">
          <PlanCard />
        </div>
      ) : null}

      <div ref={listRef} className="flex-1 overflow-y-auto px-3 py-3">
        {items.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 px-4 text-center">
            <span className="font-mono text-[12px] text-muted-dim">
              等待指令
            </span>
            <span className="text-[11.5px] leading-relaxed text-muted-dim">
              描述你想做的改动，agent 会调用工具并实时回传过程。
            </span>
          </div>
        ) : (
          <div className="space-y-2.5">
            {items.map((item) => {
              const reverted =
                "reverted" in item && Boolean(item.reverted);
              return (
                <div
                  key={item.id}
                  className={reverted ? "opacity-40" : undefined}
                  title={reverted ? "已回滚" : undefined}
                >
                  <ChatEventView item={item} streaming={streaming} />
                </div>
              );
            })}
          </div>
        )}
      </div>

      <div className="flex shrink-0 items-center justify-between border-t border-surface-border px-3 py-1.5">
        <button
          type="button"
          onClick={() => setChangesOpen(true)}
          className="flex items-center gap-1.5 text-[10.5px] text-muted-dim transition-colors hover:text-text"
          title="查看本次会话的文件变更"
        >
          变更清单
          <span className="font-mono text-[10.5px] text-text">
            {changes.length}
          </span>
        </button>
        <span className="text-[10px] text-muted-dim">检查点入口在顶栏</span>
      </div>

      <footer className="shrink-0 border-t border-surface-border p-3">
        <div className="rounded border border-surface-border bg-surface-alt transition-colors focus-within:border-[#2E343A]">
          <textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={handleKeyDown}
            rows={3}
            disabled={streaming}
            placeholder="描述你要做的改动…"
            className="w-full resize-none bg-transparent px-2.5 py-2 text-[13px] leading-relaxed text-text placeholder:text-muted-dim focus:outline-none disabled:opacity-60"
          />
          <div className="flex items-center justify-between px-2 pb-2">
            <span className="text-[10px] text-muted-dim">
              {streaming ? "运行中…" : "Enter 发送 · Shift+Enter 换行"}
            </span>
            <div className="flex items-center gap-2">
              {stats ? (
                <span className="font-mono text-[10px] text-muted-dim">
                  {stats}
                </span>
              ) : null}
              {streaming ? (
                <Button
                  variant="danger"
                  size="sm"
                  onClick={() => void stopStreaming(sessionId ?? "")}
                  title="停止当前运行"
                >
                  <span className="h-2 w-2 rounded-[1px] bg-current" />
                  停止
                </Button>
              ) : (
                <Button
                  variant="brand"
                  size="sm"
                  onClick={submit}
                  disabled={!canSend}
                >
                  <SendIcon className="h-3 w-3" />
                  发送
                </Button>
              )}
            </div>
          </div>
        </div>
      </footer>

      {changesOpen ? (
        <ChangeList onClose={() => setChangesOpen(false)} />
      ) : null}
    </aside>
  );
}

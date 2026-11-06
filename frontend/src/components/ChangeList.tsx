import { useState } from "react";

import type { ChangeRecord } from "../lib/types";
import { useChatStore } from "../stores/useChatStore";
import { useEditorStore } from "../stores/useEditorStore";
import { useSessionStore } from "../stores/useSessionStore";
import { Button } from "./ui/button";
import { RefreshIcon } from "./ui/icons";
import { Spinner } from "./ui/spinner";

const STATUS: Record<
  ChangeRecord["status"],
  { label: string; className: string }
> = {
  applied: { label: "已生效", className: "text-brand" },
  rejected: { label: "已拒绝", className: "text-muted-dim" },
  reverted: { label: "已回滚", className: "text-yellow-400" },
  failed: { label: "验证失败", className: "text-red-400" },
};

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

/** 本次会话的变更清单抽屉：倒序、含 +/- 行数与状态，点击在编辑器中打开文件。 */
export function ChangeList({ onClose }: { onClose: () => void }) {
  const sessionId = useSessionStore((state) => state.id);
  const changes = useChatStore((state) => state.changes);
  const loadChanges = useChatStore((state) => state.loadChanges);
  const [loading, setLoading] = useState(false);

  const refresh = async () => {
    if (!sessionId) return;
    setLoading(true);
    await loadChanges(sessionId);
    setLoading(false);
  };

  const openFile = (record: ChangeRecord) => {
    if (!sessionId || record.operation === "delete") return;
    void useEditorStore.getState().open(sessionId, record.path);
  };

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/50">
      <div className="flex h-full w-[400px] flex-col border-l border-surface-border bg-panel shadow-2xl">
        <header className="flex h-10 shrink-0 items-center justify-between border-b border-surface-border px-3">
          <span className="text-[11px] uppercase tracking-wider text-muted-dim">
            本次变更
          </span>
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="icon"
              title="刷新"
              disabled={loading}
              onClick={() => void refresh()}
              className="h-6 w-6"
            >
              {loading ? <Spinner size={10} /> : <RefreshIcon className="h-3 w-3" />}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={onClose}
              className="h-6 text-[11px]"
            >
              关闭
            </Button>
          </div>
        </header>

        <div className="flex-1 overflow-y-auto p-2.5">
          {changes.length === 0 ? (
            <div className="px-2 py-6 text-center text-[11.5px] text-muted-dim">
              还没有文件变更。agent 落盘后会在这里记录。
            </div>
          ) : (
            <ul className="space-y-1">
              {changes.map((record) => {
                const status = STATUS[record.status] ?? STATUS.applied;
                return (
                  <li key={record.id}>
                    <button
                      type="button"
                      disabled={record.operation === "delete"}
                      onClick={() => openFile(record)}
                      title={
                        record.operation === "delete"
                          ? "文件已删除，无法打开"
                          : "在编辑器中打开"
                      }
                      className="w-full rounded border border-surface-border bg-surface-alt px-2.5 py-1.5 text-left transition-colors hover:border-[#2E343A] disabled:cursor-default disabled:hover:border-surface-border"
                    >
                      <div className="flex items-center gap-2">
                        <span className="truncate font-mono text-[11.5px] text-text">
                          {record.path}
                        </span>
                        <span className="ml-auto shrink-0 text-[10.5px] text-muted-dim">
                          {record.operation === "create"
                            ? "新建"
                            : record.operation === "delete"
                              ? "删除"
                              : "修改"}
                        </span>
                      </div>
                      <div className="mt-1 flex items-center gap-2 font-mono text-[10.5px]">
                        <span className="text-brand">+{record.added}</span>
                        <span className="text-red-400">-{record.deleted}</span>
                        <span className={`ml-auto ${status.className}`}>
                          {status.label}
                        </span>
                        <span className="text-muted-dim">
                          {formatTime(record.created_at)}
                        </span>
                      </div>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

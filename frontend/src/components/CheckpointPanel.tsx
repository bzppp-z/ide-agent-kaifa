import { useEffect, useState } from "react";

import {
  formatError,
  getSession,
  listCheckpoints,
  rollbackCheckpoint,
} from "../lib/api";
import type { Checkpoint } from "../lib/types";
import { useChatStore } from "../stores/useChatStore";
import { useEditorStore } from "../stores/useEditorStore";
import { useSessionStore } from "../stores/useSessionStore";
import { useWorkspaceStore } from "../stores/useWorkspaceStore";
import { Button } from "./ui/button";
import { RefreshIcon } from "./ui/icons";
import { Spinner } from "./ui/spinner";

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

/** 检查点抽屉：最新在上，回滚必须二次确认；运行中禁止回滚。 */
export function CheckpointPanel({ onClose }: { onClose: () => void }) {
  const sessionId = useSessionStore((state) => state.id);
  const streaming = useChatStore((state) => state.streaming);

  const [items, setItems] = useState<Checkpoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<Checkpoint | null>(null);
  const [rolling, setRolling] = useState(false);

  const load = async () => {
    if (!sessionId) return;
    setLoading(true);
    setError(null);
    try {
      setItems(await listCheckpoints(sessionId));
    } catch (cause) {
      setError(formatError(cause));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  const rollback = async (checkpoint: Checkpoint) => {
    if (!sessionId || rolling) return;
    setRolling(true);
    setError(null);
    try {
      await rollbackCheckpoint(sessionId, checkpoint.id);

      // 恢复会话历史（reverted 标记由后端返回），刷新变更清单/文件树/标签
      const detail = await getSession(sessionId);
      useChatStore.getState().loadHistory(detail.messages);
      useChatStore.getState().setPlan(detail.plan ?? null);
      await useChatStore.getState().loadChanges(sessionId);
      await useWorkspaceStore.getState().loadTree(sessionId);
      await useEditorStore.getState().refreshAll(sessionId);

      setConfirm(null);
      await load();
    } catch (cause) {
      setError(formatError(cause));
    } finally {
      setRolling(false);
    }
  };

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/50">
      <div className="flex h-full w-[380px] flex-col border-l border-surface-border bg-panel shadow-2xl">
        <header className="flex h-10 shrink-0 items-center justify-between border-b border-surface-border px-3">
          <span className="text-[11px] uppercase tracking-wider text-muted-dim">
            检查点
          </span>
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="icon"
              title="刷新"
              disabled={loading || rolling}
              onClick={() => void load()}
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
          {error ? (
            <div className="mb-2 rounded border border-red-500/25 bg-red-500/5 px-2.5 py-1.5 text-[11px] text-red-400/90">
              {error}
            </div>
          ) : null}

          {items.length === 0 && !loading ? (
            <div className="px-2 py-6 text-center text-[11.5px] text-muted-dim">
              还没有检查点。每次运行开始时会自动创建。
            </div>
          ) : null}

          <ul className="space-y-1.5">
            {items.map((checkpoint) => (
              <li
                key={checkpoint.id}
                className="rounded border border-surface-border bg-surface-alt px-2.5 py-2"
              >
                <div className="flex items-center gap-2">
                  <span className="font-mono text-[10.5px] text-muted-dim">
                    {formatTime(checkpoint.created_at)}
                  </span>
                  <span className="ml-auto font-mono text-[10.5px] text-muted-dim">
                    {checkpoint.file_count} 个文件
                  </span>
                </div>
                <div className="mt-1 break-words text-[12px] leading-snug text-text">
                  {checkpoint.user_message || "（无用户消息）"}
                </div>
                <div className="mt-1.5 flex justify-end">
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-6 text-[11px]"
                    disabled={streaming || rolling}
                    title={streaming ? "运行中不能回滚" : "回滚到此前"}
                    onClick={() => setConfirm(checkpoint)}
                  >
                    回滚到此前
                  </Button>
                </div>
              </li>
            ))}
          </ul>

          {items.length >= 50 ? (
            <div className="mt-2 text-center text-[10.5px] text-muted-dim">
              仅保留最新 50 条，更早的检查点已淘汰
            </div>
          ) : null}
        </div>
      </div>

      {confirm ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="w-full max-w-[360px] rounded-lg border border-surface-border bg-panel p-4 shadow-xl">
            <div className="text-[13px] text-text">确认回滚？</div>
            <div className="mt-2 text-[11.5px] leading-relaxed text-muted">
              回滚会丢弃这之后的所有改动，并把之后的对话消息置灰。此操作不可撤销。
            </div>
            <div className="mt-1.5 break-all font-mono text-[10.5px] text-muted-dim">
              {formatTime(confirm.created_at)} · {confirm.file_count} 个文件
            </div>
            <div className="mt-3 flex justify-end gap-2">
              <Button
                size="sm"
                variant="ghost"
                disabled={rolling}
                onClick={() => setConfirm(null)}
              >
                取消
              </Button>
              <Button
                size="sm"
                variant="danger"
                disabled={rolling || streaming}
                onClick={() => void rollback(confirm)}
              >
                {rolling ? <Spinner size={10} /> : null}
                确认回滚
              </Button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

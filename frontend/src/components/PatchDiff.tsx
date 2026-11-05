import { useState } from "react";

import { formatError, respondPatch } from "../lib/api";
import { buildPatchSides } from "../lib/patchDiff";
import type { ChatPatchItem } from "../stores/useChatStore";
import { useChatStore } from "../stores/useChatStore";
import { useEditorStore } from "../stores/useEditorStore";
import { useSessionStore } from "../stores/useSessionStore";
import { Button } from "./ui/button";

/** patch 审批卡片：左右两栏对比 + 接受/拒绝，决议后置灰防重复点击。 */
export function PatchDiff({ item }: { item: ChatPatchItem }) {
  const setPatchStatus = useChatStore((state) => state.setPatchStatus);
  const sessionId = useSessionStore((state) => state.id);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const sides = buildPatchSides(item);
  const decided = item.status !== "pending";

  const decide = async (accepted: boolean) => {
    if (submitting || decided) return;
    setSubmitting(true);
    setError(null);
    try {
      await respondPatch(item.patchId, accepted);
      setPatchStatus(item.patchId, accepted ? "accepted" : "rejected");

      // 接受后重新加载当前打开的文件（若正好是 patch 目标）
      if (accepted && sessionId) {
        const editor = useEditorStore.getState();
        if (editor.filePath === item.path) {
          await editor.open(sessionId, item.path);
        }
      }
    } catch (cause) {
      setError(formatError(cause));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="rounded border border-surface-border border-l-2 border-l-brand/60 bg-surface-alt px-2.5 py-2">
      <div className="flex items-center gap-2 text-[11px] text-muted">
        <span>建议修改</span>
        <span className="font-mono text-[11.5px] text-text">{item.path}</span>
        {item.status === "accepted" ? (
          <span className="ml-auto text-brand">已接受</span>
        ) : item.status === "rejected" ? (
          <span className="ml-auto text-red-400/90">已拒绝</span>
        ) : (
          <span className="ml-auto text-muted-dim">待审批</span>
        )}
      </div>

      <div className="mt-1.5 grid grid-cols-2 gap-1.5">
        <div className="min-w-0 overflow-hidden rounded border border-red-500/20">
          <div className="border-b border-red-500/20 bg-red-500/5 px-2 py-0.5 text-[10px] text-red-400/80">
            修改前
          </div>
          <pre className="max-h-44 overflow-auto whitespace-pre-wrap break-words bg-bg px-2 py-1.5 font-mono text-[10.5px] leading-relaxed text-red-400/85">
            {sides.left.join("\n")}
          </pre>
        </div>
        <div className="min-w-0 overflow-hidden rounded border border-brand/20">
          <div className="border-b border-brand/20 bg-brand/5 px-2 py-0.5 text-[10px] text-brand/80">
            修改后
          </div>
          <pre className="max-h-44 overflow-auto whitespace-pre-wrap break-words bg-bg px-2 py-1.5 font-mono text-[10.5px] leading-relaxed text-brand/90">
            {sides.right.join("\n")}
          </pre>
        </div>
      </div>

      <div className="mt-2 flex items-center gap-2">
        <Button
          variant="brand"
          size="sm"
          disabled={decided || submitting}
          onClick={() => void decide(true)}
        >
          接受
        </Button>
        <Button
          variant="danger"
          size="sm"
          disabled={decided || submitting}
          onClick={() => void decide(false)}
        >
          拒绝
        </Button>
        {submitting ? (
          <span className="text-[10.5px] text-muted-dim">提交中…</span>
        ) : null}
        {error ? (
          <span className="text-[10.5px] text-red-400/90">{error}</span>
        ) : null}
      </div>
    </div>
  );
}

import { useState } from "react";

import { formatError, respondChangeSet } from "../lib/api";
import type { ChangeFile } from "../lib/types";
import type { ChatChangeSetItem, ChatPatchItem } from "../stores/useChatStore";
import { useChatStore } from "../stores/useChatStore";
import { PatchDiff } from "./PatchDiff";
import { Button } from "./ui/button";

function asPatchItem(
  item: ChatChangeSetItem,
  file: ChangeFile,
): ChatPatchItem {
  const result = item.results[file.path];
  return {
    id: `${item.id}-${file.path}`,
    kind: "patch",
    patchId: "",
    path: file.path,
    search: "",
    replace: "",
    before: file.before ?? "",
    after: file.after ?? "",
    diff: file.diff,
    status:
      result === "applied" ? "accepted" : result === "rejected" ? "rejected" : "pending",
  };
}

function statusLabel(item: ChatChangeSetItem, path: string): string {
  if (item.status === "submitting") return "处理中…";
  const result = item.results[path];
  if (result === "applied") return "已生效";
  if (result === "rejected") return "已拒绝";
  if (result === "failed") return "冲突/失败";
  return "待审批";
}

/** 多文件变更审批卡片：逐文件 diff + 接受/拒绝 + 删除二次确认。 */
export function ChangeSetCard({ item }: { item: ChatChangeSetItem }) {
  const setChangeSetResults = useChatStore((state) => state.setChangeSetResults);
  const [decisions, setDecisions] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(item.files.map((file) => [file.path, true])),
  );
  const [confirmedDeletes, setConfirmedDeletes] = useState<Record<string, boolean>>(
    {},
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(item.error ?? null);

  const decided = item.status === "done";
  const readonly = item.auto;

  const hasUnconfirmedDelete = item.files.some(
    (file) =>
      file.operation === "delete" &&
      decisions[file.path] &&
      !confirmedDeletes[file.path],
  );

  const allAccept = item.files.every((file) => decisions[file.path]);
  const allReject = item.files.every((file) => !decisions[file.path]);

  const submit = async (mode: "all" | "none" | "current") => {
    if (submitting || decided || readonly) return;
    if (mode !== "none" && hasUnconfirmedDelete) return;

    const finalDecisions: Record<string, boolean> = {};
    for (const file of item.files) {
      finalDecisions[file.path] =
        mode === "all" ? true : mode === "none" ? false : Boolean(decisions[file.path]);
    }

    setSubmitting(true);
    setError(null);
    setDecisions(finalDecisions);
    try {
      const response = await respondChangeSet(item.changeSetId, finalDecisions);
      const applied = new Set(response.applied ?? []);
      const rejected = new Set(response.rejected ?? []);
      const failed = new Set(response.failed ?? []);
      const results: Record<string, "applied" | "rejected" | "failed"> = {};
      for (const file of item.files) {
        if (applied.has(file.path)) results[file.path] = "applied";
        else if (rejected.has(file.path)) results[file.path] = "rejected";
        else if (failed.has(file.path)) results[file.path] = "failed";
        else results[file.path] = finalDecisions[file.path] ? "failed" : "rejected";
      }
      setChangeSetResults(item.id, results, null);
    } catch (cause) {
      setError(formatError(cause));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="rounded border border-surface-border border-l-2 border-l-brand/60 bg-surface-alt px-2.5 py-2">
      <div className="flex items-center gap-2 text-[11px] text-muted">
        <span>变更批次</span>
        <span className="font-mono text-[11.5px] text-text">
          {item.files.length} 个文件
        </span>
        {item.verification ? (
          <span
            className={
              item.verification === "passed"
                ? "rounded border border-brand/40 bg-brand/10 px-1 py-0.5 text-[10px] text-brand"
                : "rounded border border-red-500/40 bg-red-500/10 px-1 py-0.5 text-[10px] text-red-400/90"
            }
          >
            {item.verification === "passed" ? "已验证" : "验证失败"}
          </span>
        ) : null}
        <span className="ml-auto text-muted-dim">
          {readonly
            ? item.autoApplied
              ? "已自动生效"
              : "自动生效失败"
            : item.status === "done"
              ? "已处理"
              : item.status === "submitting"
                ? "提交中…"
                : "待审批"}
        </span>
      </div>

      <div className="mt-1.5 space-y-2">
        {item.files.map((file) => {
          const isDelete = file.operation === "delete";
          const result = item.results[file.path];
          return (
            <div
              key={file.path}
              className={
                isDelete
                  ? "rounded border border-red-500/30 bg-red-500/5 p-1.5"
                  : "rounded border border-surface-border p-1.5"
              }
            >
              <div className="mb-1 flex flex-wrap items-center gap-2 text-[11px]">
                <span className="font-mono text-[11.5px] text-text">{file.path}</span>
                <span className="rounded bg-surface px-1 py-0.5 text-[10px] text-muted-dim">
                  {file.operation === "create"
                    ? "新建"
                    : file.operation === "delete"
                      ? "删除"
                      : "修改"}
                </span>
                {file.note ? (
                  <span className="text-[10px] text-yellow-400/90">{file.note}</span>
                ) : null}
                <span
                  className={
                    result === "applied"
                      ? "ml-auto text-brand"
                      : result === "failed"
                        ? "ml-auto text-red-400/90"
                        : "ml-auto text-muted-dim"
                  }
                >
                  {statusLabel(item, file.path)}
                </span>
              </div>

              {isDelete ? (
                <div className="mb-1.5 flex items-center gap-2 rounded border border-red-500/30 bg-red-500/10 px-2 py-1 text-[10.5px] text-red-400/90">
                  <span>将删除此文件，操作不可恢复</span>
                  {decided || readonly ? null : (
                    <label className="ml-auto flex cursor-pointer items-center gap-1">
                      <input
                        type="checkbox"
                        checked={Boolean(confirmedDeletes[file.path])}
                        onChange={(event) =>
                          setConfirmedDeletes((current) => ({
                            ...current,
                            [file.path]: event.target.checked,
                          }))
                        }
                        className="h-3 w-3 accent-red-500"
                      />
                      确认删除
                    </label>
                  )}
                </div>
              ) : null}

              {readonly || decided ? null : (
                <div className="mb-1.5 flex items-center gap-1.5">
                  <button
                    type="button"
                    disabled={submitting}
                    onClick={() =>
                      setDecisions((current) => ({
                        ...current,
                        [file.path]: true,
                      }))
                    }
                    className={`rounded border px-1.5 py-0.5 text-[10.5px] ${
                      decisions[file.path]
                        ? "border-brand/40 bg-brand/10 text-brand"
                        : "border-surface-border text-muted-dim hover:text-muted"
                    }`}
                  >
                    接受
                  </button>
                  <button
                    type="button"
                    disabled={submitting}
                    onClick={() =>
                      setDecisions((current) => ({
                        ...current,
                        [file.path]: false,
                      }))
                    }
                    className={`rounded border px-1.5 py-0.5 text-[10.5px] ${
                      !decisions[file.path]
                        ? "border-red-500/40 bg-red-500/10 text-red-400/90"
                        : "border-surface-border text-muted-dim hover:text-muted"
                    }`}
                  >
                    拒绝
                  </button>
                </div>
              )}

              <PatchDiff item={asPatchItem(item, file)} compact />
            </div>
          );
        })}
      </div>

      {readonly ? null : (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <Button
            variant="brand"
            size="sm"
            disabled={decided || submitting || hasUnconfirmedDelete}
            onClick={() => void submit("all")}
          >
            整批接受
          </Button>
          {!allAccept && !allReject ? (
            <Button
              variant="brand"
              size="sm"
              disabled={decided || submitting || hasUnconfirmedDelete}
              onClick={() => void submit("current")}
            >
              提交当前选择
            </Button>
          ) : null}
          <Button
            variant="danger"
            size="sm"
            disabled={decided || submitting}
            onClick={() => void submit("none")}
          >
            整批拒绝
          </Button>
          {hasUnconfirmedDelete ? (
            <span className="text-[10.5px] text-red-400/80">请先勾选确认删除</span>
          ) : null}
          {submitting ? (
            <span className="text-[10.5px] text-muted-dim">提交中…</span>
          ) : null}
          {error ? (
            <span className="text-[10.5px] text-red-400/90">{error}</span>
          ) : null}
        </div>
      )}
    </div>
  );
}

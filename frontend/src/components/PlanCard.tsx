import type { PlanItem, PlanItemStatus } from "../lib/types";
import { cn } from "../lib/cn";
import { useChatStore } from "../stores/useChatStore";

const STATUS_LABEL: Record<PlanItemStatus, string> = {
  pending: "待开始",
  in_progress: "进行中",
  done: "完成",
  failed: "失败",
  skipped: "跳过",
};

function StatusIcon({ status }: { status: PlanItemStatus }) {
  if (status === "in_progress") {
    return (
      <span
        className="mt-1 h-2 w-2 shrink-0 animate-pulse rounded-full bg-brand"
        title={STATUS_LABEL[status]}
      />
    );
  }

  const glyph =
    status === "done" ? "✓" : status === "failed" ? "✗" : status === "skipped" ? "–" : "○";

  return (
    <span
      className={cn(
        "mt-0.5 inline-flex h-3 w-3 shrink-0 items-center justify-center font-mono text-[11px] leading-none",
        status === "done" && "text-muted",
        status === "failed" && "text-red-400",
        status === "skipped" && "text-muted-dim",
        status === "pending" && "text-muted-dim",
      )}
      title={STATUS_LABEL[status]}
    >
      {glyph}
    </span>
  );
}

function PlanRow({ item }: { item: PlanItem }) {
  return (
    <li className="flex items-start gap-2">
      <StatusIcon status={item.status} />
      <div className="min-w-0 flex-1">
        <div
          className={cn(
            "break-words text-[12px] leading-snug",
            item.status === "in_progress" && "font-medium text-text",
            item.status === "pending" && "text-muted",
            item.status === "done" && "text-muted",
            item.status === "failed" && "text-red-400/90",
            item.status === "skipped" &&
              "text-muted-dim line-through decoration-muted-dim/40",
          )}
        >
          {item.title}
        </div>
        {item.note ? (
          <div
            className={cn(
              "mt-0.5 break-words text-[10.5px] leading-relaxed",
              item.status === "failed" ? "text-red-400/70" : "text-muted-dim",
            )}
          >
            {item.note}
          </div>
        ) : null}
      </div>
    </li>
  );
}

/** 对话面板顶部的计划卡片：随 plan.update 更新，刷新后由 session detail 恢复。 */
export function PlanCard() {
  const plan = useChatStore((state) => state.plan);
  if (!plan || plan.items.length === 0) return null;

  const done = plan.items.filter((item) => item.status === "done").length;

  return (
    <section className="max-h-64 overflow-y-auto rounded-lg border border-surface-border bg-surface-alt px-3 py-2.5">
      <header className="mb-2 flex items-center justify-between">
        <span className="text-[10.5px] uppercase tracking-wider text-muted-dim">
          本次计划
        </span>
        <span className="font-mono text-[10.5px] text-muted-dim">
          {done}/{plan.items.length} 完成
        </span>
      </header>
      <ul className="space-y-1.5">
        {plan.items.map((item) => (
          <PlanRow key={item.id} item={item} />
        ))}
      </ul>
    </section>
  );
}

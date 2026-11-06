import { create } from "zustand";

import { formatError, runAgent, stopRun } from "../lib/api";
import type { Message, PlanState, SSEEvent } from "../lib/types";

// ------------------------------------------------------------------ chat items

export interface ChatUserItem {
  id: string;
  kind: "user";
  content: string;
  createdAt: number;
}

export interface ChatAssistantItem {
  id: string;
  kind: "assistant";
  content: string;
  streaming: boolean;
}

export interface ChatToolItem {
  id: string;
  kind: "tool";
  callId: string;
  toolName: string;
  args: Record<string, unknown>;
  state: "calling" | "ok" | "error";
  result?: unknown;
  durationMs?: number;
}

export interface ChatPatchItem {
  id: string;
  kind: "patch";
  patchId: string;
  path: string;
  search: string;
  replace: string;
  before?: string;
  after?: string;
  diff?: string;
  status: "pending" | "accepted" | "rejected";
}

export interface ChatOutputItem {
  id: string;
  kind: "output";
  stream: "stdout" | "stderr";
  chunk: string;
}

export interface ChatErrorItem {
  id: string;
  kind: "error";
  message: string;
  code: string | null;
}

export interface ChatDoneItem {
  id: string;
  kind: "done";
  warning: string | null;
  totalTokens: number;
  totalSteps: number;
  stopped?: boolean;
}

export type ChatItem =
  | ChatUserItem
  | ChatAssistantItem
  | ChatToolItem
  | ChatPatchItem
  | ChatOutputItem
  | ChatErrorItem
  | ChatDoneItem;

// --------------------------------------------------------------------- store

let sequence = 0;

function nextId(prefix: string): string {
  sequence += 1;
  return `${prefix}-${Date.now()}-${sequence}`;
}

interface ChatState {
  items: ChatItem[];
  /** 当前 run 的任务计划；刷新后由 session detail 恢复。 */
  plan: PlanState | null;
  streaming: boolean;
  runId: string | null;
  error: string | null;
  /** 当前 SSE fetch 的 AbortController（用于「停止」按钮）。 */
  sseAbort: AbortController | null;
  sendMessage: (sessionId: string, text: string) => Promise<void>;
  pushEvent: (event: SSEEvent) => void;
  /** 刷新恢复：用 GET session 返回的历史消息重建气泡（不覆盖正在流式的 run）。 */
  loadHistory: (messages: Message[]) => void;
  setPlan: (plan: PlanState | null) => void;
  setSseAbort: (controller: AbortController | null) => void;
  /** 标记某个 patch 卡片的处理结果（PatchDiff 在决议成功后调用）。 */
  setPatchStatus: (patchId: string, status: "accepted" | "rejected") => void;
  /** 主动停止：先通知后端取消 run，再断开 SSE 并把在写消息收尾。 */
  stopStreaming: (sessionId: string) => Promise<void>;
  endStream: () => void;
  clear: () => void;
}

export const useChatStore = create<ChatState>((set, get) => ({
  items: [],
  plan: null,
  streaming: false,
  runId: null,
  error: null,
  sseAbort: null,

  sendMessage: async (sessionId, text) => {
    const message = text.trim();
    if (!message || get().streaming) return;

    const userItem: ChatUserItem = {
      id: nextId("user"),
      kind: "user",
      content: message,
      createdAt: Date.now(),
    };
    set((state) => ({
      items: [
        ...state.items.map((item) =>
          item.kind === "assistant" ? { ...item, streaming: false } : item,
        ),
        userItem,
      ],
      error: null,
    }));

    try {
      const created = await runAgent(sessionId, message);
      // run 登记成功后才置 streaming，useSSE 看到标记后再连流，避免先连到 404。
      set({ runId: created.run_id, streaming: true });
    } catch (error) {
      const detail = formatError(error);
      set((state) => ({
        items: [
          ...state.items,
          {
            id: nextId("error"),
            kind: "error",
            message: detail,
            code: "run_failed",
          },
        ],
        streaming: false,
        runId: null,
        error: detail,
      }));
    }
  },

  pushEvent: (event) => {
    set((state) => {
      const items = [...state.items];

      switch (event.type) {
        case "message.delta": {
          const last = items[items.length - 1];
          if (last && last.kind === "assistant" && last.streaming) {
            items[items.length - 1] = {
              ...last,
              content: last.content + event.content,
            };
          } else {
            items.push({
              id: nextId("assistant"),
              kind: "assistant",
              content: event.content,
              streaming: true,
            });
          }
          return { items };
        }

        case "tool.call": {
          items.push({
            id: nextId("tool"),
            kind: "tool",
            callId: event.call_id,
            toolName: event.tool_name,
            args: event.arguments ?? {},
            state: "calling",
          });
          return { items };
        }

        case "tool.result": {
          let index = -1;
          for (let i = items.length - 1; i >= 0; i -= 1) {
            const item = items[i];
            if (
              item.kind === "tool" &&
              item.callId === event.call_id &&
              item.state === "calling"
            ) {
              index = i;
              break;
            }
          }
          if (index === -1) {
            for (let i = items.length - 1; i >= 0; i -= 1) {
              const item = items[i];
              if (item.kind === "tool" && item.callId === event.call_id) {
                index = i;
                break;
              }
            }
          }
          if (index === -1) {
            items.push({
              id: nextId("tool"),
              kind: "tool",
              callId: event.call_id,
              toolName: event.tool_name,
              args: {},
              state: event.ok ? "ok" : "error",
              result: event.result,
              durationMs: event.duration_ms,
            });
          } else {
            const existing = items[index] as ChatToolItem;
            items[index] = {
              ...existing,
              state: event.ok ? "ok" : "error",
              result: event.result,
              durationMs: event.duration_ms,
            };
          }
          return { items };
        }

        case "patch.proposed": {
          items.push({
            id: nextId("patch"),
            kind: "patch",
            patchId: event.patch_id,
            path: event.path,
            search: event.search,
            replace: event.replace,
            before: event.before,
            after: event.after,
            diff: event.diff,
            status: "pending",
          });
          return { items };
        }

        case "plan.update": {
          const plan = event.plan;
          if (!plan || !Array.isArray(plan.items) || plan.items.length === 0) {
            return { items };
          }
          return { items, plan };
        }

        case "run.output": {
          const last = items[items.length - 1];
          if (last && last.kind === "output" && last.stream === event.stream) {
            items[items.length - 1] = {
              ...last,
              chunk: last.chunk + event.chunk,
            };
          } else {
            items.push({
              id: nextId("output"),
              kind: "output",
              stream: event.stream,
              chunk: event.chunk,
            });
          }
          return { items };
        }

        case "error": {
          items.push({
            id: nextId("error"),
            kind: "error",
            message: event.message,
            code: event.code ?? null,
          });
          return { items };
        }

        case "done": {
          const finalized = items.map((item) =>
            item.kind === "assistant" ? { ...item, streaming: false } : item,
          );
          finalized.push({
            id: nextId("done"),
            kind: "done",
            warning: event.warning ?? null,
            totalTokens: event.total_tokens ?? 0,
            totalSteps: event.total_steps ?? 0,
            stopped: event.stopped ?? false,
          });
          return {
            items: finalized,
            streaming: false,
            runId: null,
            sseAbort: null,
          };
        }

        default:
          return { items };
      }
    });
  },

  setSseAbort: (controller) => set({ sseAbort: controller }),

  setPlan: (plan) =>
    set({ plan: plan && Array.isArray(plan.items) && plan.items.length > 0 ? plan : null }),

  loadHistory: (messages) => {
    if (get().streaming) return;

    const items: ChatItem[] = [];
    for (const message of messages) {
      if (message.role === "user") {
        items.push({
          id: nextId("user"),
          kind: "user",
          content: message.content,
          createdAt: Date.parse(message.created_at) || Date.now(),
        });
        continue;
      }
      if (message.role !== "assistant") continue;

      const patch = parsePatchMessage(message.content);
      if (patch) {
        items.push(patch);
        continue;
      }
      if (message.content.trim()) {
        items.push({
          id: nextId("assistant"),
          kind: "assistant",
          content: message.content,
          streaming: false,
        });
      }
    }
    set({ items });
  },

  setPatchStatus: (patchId, status) =>
    set((state) => ({
      items: state.items.map((item) =>
        item.kind === "patch" && item.patchId === patchId
          ? { ...item, status }
          : item,
      ),
    })),

  stopStreaming: async (sessionId) => {
    try {
      await stopRun(sessionId);
    } catch {
      // 后端可能已经结束；本地仍按用户意愿收尾
    }
    get().sseAbort?.abort();
    set((state) => {
      const items = state.items.map((item) =>
        item.kind === "assistant" ? { ...item, streaming: false } : item,
      );
      const last = items[items.length - 1];
      if (!last || last.kind !== "done") {
        items.push({
          id: nextId("done"),
          kind: "done",
          warning: "已停止",
          totalTokens: 0,
          totalSteps: 0,
          stopped: true,
        });
      }
      return { items, streaming: false, runId: null, sseAbort: null };
    });
  },

  endStream: () =>
    set((state) => ({
      streaming: false,
      runId: null,
      sseAbort: null,
      items: state.items.map((item) =>
        item.kind === "assistant" ? { ...item, streaming: false } : item,
      ),
    })),

  clear: () =>
    set({
      items: [],
      plan: null,
      streaming: false,
      runId: null,
      error: null,
      sseAbort: null,
    }),
}));

export type { SSEEvent as SseEvent } from "../lib/types";

/**
 * 后端把 patch.proposed 事件整体存成一条 assistant 消息（JSON 字符串）。
 * 刷新恢复时把它还原成审批卡片；解析失败则按普通文本处理。
 */
function parsePatchMessage(content: string): ChatPatchItem | null {
  const trimmed = content.trim();
  if (!trimmed.startsWith("{")) return null;

  let parsed: Record<string, unknown>;
  try {
    parsed = JSON.parse(trimmed) as Record<string, unknown>;
  } catch {
    return null;
  }
  if (parsed.type !== "patch.proposed") return null;

  const patchId = parsed.patch_id;
  const path = parsed.path;
  if (typeof patchId !== "string" || typeof path !== "string") return null;

  const asString = (value: unknown): string | undefined =>
    typeof value === "string" ? value : undefined;

  return {
    id: nextId("patch"),
    kind: "patch",
    patchId,
    path,
    search: asString(parsed.search) ?? "",
    replace: asString(parsed.replace) ?? "",
    before: asString(parsed.before),
    after: asString(parsed.after),
    diff: asString(parsed.diff),
    status: "pending",
  };
}

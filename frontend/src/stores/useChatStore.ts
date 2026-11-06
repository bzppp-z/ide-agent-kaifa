import { create } from "zustand";

import { formatError, listChanges, runAgent, stopRun } from "../lib/api";
import type {
  ChangeFile,
  ChangeRecord,
  Message,
  PlanState,
  SSEEvent,
} from "../lib/types";
import { useEditorStore } from "./useEditorStore";
import { useSessionStore } from "./useSessionStore";
import { useWorkspaceStore } from "./useWorkspaceStore";

// ------------------------------------------------------------------ chat items

export interface ChatUserItem {
  id: string;
  kind: "user";
  content: string;
  createdAt: number;
  /** 检查点回滚后，位于回滚点之后的消息置灰。 */
  reverted?: boolean;
}

export interface ChatAssistantItem {
  id: string;
  kind: "assistant";
  content: string;
  streaming: boolean;
  reverted?: boolean;
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

export type ChangeSetFileStatus =
  | "pending"
  | "applied"
  | "rejected"
  | "failed";

export interface ChatChangeSetItem {
  id: string;
  kind: "change_set";
  changeSetId: string;
  files: ChangeFile[];
  auto: boolean;
  autoApplied: boolean;
  status: "pending" | "submitting" | "done";
  results: Record<string, ChangeSetFileStatus>;
  error?: string | null;
  /** 最近一次 run_command 的验证结论（徽标展示）。 */
  verification?: "passed" | "failed" | null;
}

export type ChatCommandStatus =
  | "pending"
  | "running"
  | "success"
  | "failed"
  | "rejected";

export interface ChatCommandItem {
  id: string;
  kind: "command";
  commandId: string;
  callId: string;
  command: string;
  cwd: string;
  reason: string;
  timeoutSeconds: number;
  status: ChatCommandStatus;
  output: string;
  ok: boolean | null;
  exitCode: number | null;
  durationMs: number | null;
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

/** 上下文压缩提示条：summary 为空表示压缩失败、已降级截断。 */
export interface ChatContextItem {
  id: string;
  kind: "context";
  summary: string;
  warning: string | null;
  keptMessages: number;
  droppedMessages: number;
}

export type ChatItem =
  | ChatUserItem
  | ChatAssistantItem
  | ChatToolItem
  | ChatPatchItem
  | ChatChangeSetItem
  | ChatCommandItem
  | ChatOutputItem
  | ChatErrorItem
  | ChatDoneItem
  | ChatContextItem;

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
  /** 会话变更清单（GET /sessions/{id}/changes）。 */
  changes: ChangeRecord[];
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
  loadChanges: (sessionId: string) => Promise<void>;
  setSseAbort: (controller: AbortController | null) => void;
  /** 标记某个 patch 卡片的处理结果（PatchDiff 在决议成功后调用）。 */
  setPatchStatus: (patchId: string, status: "accepted" | "rejected") => void;
  /** 标记变更批次卡片的提交状态与逐文件结果。 */
  setChangeSetResults: (
    itemId: string,
    results: Record<string, ChangeSetFileStatus>,
    error?: string | null,
  ) => void;
  /** 命令卡片在响应审批后立即切换状态（不等 tool.result）。 */
  setCommandStatus: (commandId: string, status: ChatCommandStatus) => void;
  /** 主动停止：先通知后端取消 run，再断开 SSE 并把在写消息收尾。 */
  stopStreaming: (sessionId: string) => Promise<void>;
  endStream: () => void;
  clear: () => void;
}

export const useChatStore = create<ChatState>((set, get) => ({
  items: [],
  plan: null,
  changes: [],
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
    // file.changed 没有对应聊天气泡，直接刷新文件树与已打开标签。
    if (event.type === "file.changed") {
      const sessionId = useSessionStore.getState().id;
      if (sessionId) {
        void useWorkspaceStore.getState().loadTree(sessionId);
        void useEditorStore
          .getState()
          .handleFileChanged(sessionId, event.path, event.operation);
      }
      return;
    }

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
          // run_command 由 CommandCard 承载，避免与工具卡片重复展示。
          if (event.tool_name === "run_command") {
            return { items };
          }
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
          const resultRecord =
            event.result && typeof event.result === "object"
              ? (event.result as { extra?: Record<string, unknown> })
              : null;
          const extra: Record<string, unknown> = resultRecord?.extra ?? {};
          const exitCode =
            typeof extra.exit_code === "number" ? extra.exit_code : null;

          if (event.tool_name === "run_command") {
            // 验证结论同步到最近一个变更批次卡片。
            for (let i = items.length - 1; i >= 0; i -= 1) {
              const item = items[i];
              if (item.kind === "change_set") {
                items[i] = {
                  ...item,
                  verification: event.ok ? "passed" : "failed",
                };
                break;
              }
            }
          }

          let commandIndex = -1;
          if (event.tool_name === "run_command") {
            for (let i = items.length - 1; i >= 0; i -= 1) {
              const item = items[i];
              if (item.kind === "command" && item.callId === event.call_id) {
                commandIndex = i;
                break;
              }
            }
          }
          if (commandIndex !== -1) {
            const existing = items[commandIndex] as ChatCommandItem;
            items[commandIndex] = {
              ...existing,
              status:
                extra.rejected === true
                  ? "rejected"
                  : event.ok
                    ? "success"
                    : "failed",
              ok: event.ok,
              exitCode,
              durationMs: event.duration_ms,
            };
            return { items };
          }

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

        case "change_set.proposed": {
          const auto = Boolean(event.auto);
          const status: ChangeSetFileStatus = auto
            ? event.auto_applied
              ? "applied"
              : "failed"
            : "pending";
          const results: Record<string, ChangeSetFileStatus> = {};
          for (const file of event.files ?? []) {
            results[file.path] = status;
          }
          items.push({
            id: nextId("change-set"),
            kind: "change_set",
            changeSetId: event.change_set_id,
            files: event.files ?? [],
            auto,
            autoApplied: Boolean(event.auto_applied),
            status: auto ? "done" : "pending",
            results,
            error: auto && !event.auto_applied ? "自动落盘失败" : null,
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

        case "command.proposed": {
          items.push({
            id: nextId("command"),
            kind: "command",
            commandId: event.command_id,
            callId: event.call_id ?? "",
            command: event.command,
            cwd: event.cwd ?? "",
            reason: event.reason ?? "",
            timeoutSeconds: event.timeout_seconds ?? 0,
            status: "pending",
            output: "",
            ok: null,
            exitCode: null,
            durationMs: null,
          });
          return { items };
        }

        case "run.output": {
          if (event.call_id) {
            for (let i = items.length - 1; i >= 0; i -= 1) {
              const item = items[i];
              if (item.kind === "command" && item.callId === event.call_id) {
                items[i] = {
                  ...item,
                  output: item.output + event.chunk,
                  status: item.status === "pending" ? "running" : item.status,
                };
                return { items };
              }
            }
          }
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

        case "context.compressed": {
          items.push({
            id: nextId("context"),
            kind: "context",
            summary: event.summary ?? "",
            warning: event.warning ?? null,
            keptMessages: event.kept_messages ?? 0,
            droppedMessages: event.dropped_messages ?? 0,
          });
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

  loadChanges: async (sessionId) => {
    try {
      const changes = await listChanges(sessionId);
      set({ changes });
    } catch {
      // 变更清单是辅助信息，失败不打断对话
    }
  },

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
          reverted: Boolean(message.reverted),
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
          reverted: Boolean(message.reverted),
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

  setChangeSetResults: (itemId, results, error = null) =>
    set((state) => ({
      items: state.items.map((item) =>
        item.kind === "change_set" && item.id === itemId
          ? { ...item, status: "done", results, error }
          : item,
      ),
    })),

  setCommandStatus: (commandId, status) =>
    set((state) => ({
      items: state.items.map((item) =>
        item.kind === "command" && item.commandId === commandId
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
      changes: [],
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

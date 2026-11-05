import { create } from "zustand";

import { formatError, runAgent } from "../lib/api";
import type { SSEEvent } from "../lib/types";

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
  streaming: boolean;
  runId: string | null;
  error: string | null;
  /** 当前 SSE fetch 的 AbortController（用于「停止」按钮）。 */
  sseAbort: AbortController | null;
  sendMessage: (sessionId: string, text: string) => Promise<void>;
  pushEvent: (event: SSEEvent) => void;
  setSseAbort: (controller: AbortController | null) => void;
  /** 主动停止：断开 SSE 并把在写消息收尾。 */
  stopStreaming: () => void;
  endStream: () => void;
  clear: () => void;
}

export const useChatStore = create<ChatState>((set, get) => ({
  items: [],
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
          });
          return { items };
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

  stopStreaming: () => {
    get().sseAbort?.abort();
    set((state) => ({
      streaming: false,
      runId: null,
      sseAbort: null,
      items: state.items.map((item) =>
        item.kind === "assistant" ? { ...item, streaming: false } : item,
      ),
    }));
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
      streaming: false,
      runId: null,
      error: null,
      sseAbort: null,
    }),
}));

export type { SSEEvent as SseEvent } from "../lib/types";

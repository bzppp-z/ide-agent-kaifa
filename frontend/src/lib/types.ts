/** 后端 API / SSE 契约类型（与 backend 的 pydantic schema 一一对应）。 */

export interface Session {
  id: string;
  demo_repo: string;
  container_ready: boolean;
  created_at: string;
  /** 本地目录会话的真实根路径；demo 会话为 null/undefined。 */
  local_path?: string | null;
}

export interface SessionCreated extends Session {
  /** JWT，仅存内存。 */
  token: string;
}

export interface Message {
  id: string;
  role: "user" | "assistant" | string;
  content: string;
  created_at: string;
}

export interface SessionDetail extends Session {
  messages: Message[];
}

export interface FileEntry {
  name: string;
  path: string;
  type: "dir" | "file";
  size: number;
}

export interface FileContent {
  type: "dir" | "file";
  content: string | null;
  entries: FileEntry[] | null;
  truncated: boolean;
}

export interface RunCreated {
  run_id: string;
}

export interface FileWriteResult {
  ok: true;
  path: string;
}

export interface PatchDecisionResult {
  ok: true;
  patch_id: string;
  accepted: boolean;
}

/** 用户自定义模型 Provider（api_key 只返回掩码）。 */
export interface Provider {
  id: string;
  name: string;
  base_url: string;
  model: string;
  api_key_masked: string;
  is_active: boolean;
  created_at: string;
}

export interface LocalBrowseEntry {
  name: string;
  path: string;
  type: "dir";
}

export interface LocalBrowseResponse {
  path: string;
  parent: string | null;
  entries: LocalBrowseEntry[];
}

export interface ProviderTestResult {
  ok: boolean;
  message: string;
  latency_ms: number;
}

export type SSEEvent =
  | { type: "message.delta"; content: string }
  | {
      type: "tool.call";
      call_id: string;
      tool_name: string;
      arguments: Record<string, unknown>;
    }
  | {
      type: "tool.result";
      call_id: string;
      tool_name: string;
      ok: boolean;
      result: unknown;
      duration_ms: number;
    }
  | {
      type: "patch.proposed";
      patch_id: string;
      path: string;
      search: string;
      replace: string;
      before?: string;
      after?: string;
      diff?: string;
    }
  | { type: "run.output"; stream: "stdout" | "stderr"; chunk: string }
  | { type: "error"; message: string; code?: string | null }
  | {
      type: "done";
      run_id?: string | null;
      warning?: string | null;
      total_tokens: number;
      total_steps: number;
    };

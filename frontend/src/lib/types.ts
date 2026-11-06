/** 后端 API / SSE 契约类型（与 backend 的 pydantic schema 一一对应）。 */

export type PermissionMode = "readonly" | "approval" | "auto";

export interface Session {
  id: string;
  demo_repo: string;
  container_ready: boolean;
  created_at: string;
  /** 本地目录会话的真实根路径；demo 会话为 null/undefined。 */
  local_path?: string | null;
  permission_mode?: PermissionMode;
  plan?: PlanState | null;
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
  /** 回滚检查点后被置灰的历史消息。 */
  reverted?: boolean;
}

export interface SessionDetail extends Session {
  /** 最近一次上下文压缩生成的结构化摘要。 */
  summary_text?: string | null;
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

export interface ChangeSetDecisionResult {
  ok: true;
  change_set_id: string;
  applied: string[];
  rejected: string[];
  failed: string[];
}

export interface CommandDecisionResult {
  ok: true;
  command_id: string;
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

export type PlanItemStatus =
  | "pending"
  | "in_progress"
  | "done"
  | "failed"
  | "skipped";

export interface PlanItem {
  id: string;
  title: string;
  status: PlanItemStatus;
  note?: string | null;
}

export interface PlanState {
  items: PlanItem[];
}

export interface ChangeFile {
  path: string;
  operation: "create" | "update" | "delete";
  before?: string;
  after?: string;
  diff?: string;
  /** 预览阶段的内容哈希，用于落盘前冲突检测。 */
  sha?: string;
  exists?: boolean;
  note?: string | null;
  index?: number;
}

export interface Checkpoint {
  id: string;
  session_id: string;
  created_at: string;
  user_message: string;
  file_count: number;
}

export interface ChangeRecord {
  id: string;
  path: string;
  operation: "create" | "update" | "delete";
  added: number;
  deleted: number;
  status: "applied" | "rejected" | "reverted" | "failed";
  created_at: string;
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
  | {
      type: "run.output";
      stream: "stdout" | "stderr";
      chunk: string;
      call_id?: string;
    }
  | { type: "plan.update"; plan: PlanState }
  | {
      type: "change_set.proposed";
      change_set_id: string;
      files: ChangeFile[];
      /** auto 模式无需审批；auto_applied 表示是否已成功落盘。 */
      auto?: boolean;
      auto_applied?: boolean;
    }
  | {
      type: "command.proposed";
      command_id: string;
      command: string;
      cwd: string;
      timeout_seconds: number;
      reason: string;
      call_id?: string;
    }
  | {
      type: "context.compressed";
      summary: string;
      kept_messages: number;
      dropped_messages: number;
      warning?: string | null;
    }
  | { type: "file.changed"; path: string; operation: "write" | "delete" | "rollback" }
  | { type: "error"; message: string; code?: string | null }
  | {
      type: "done";
      run_id?: string | null;
      warning?: string | null;
      total_tokens: number;
      total_steps: number;
      stopped?: boolean;
    };

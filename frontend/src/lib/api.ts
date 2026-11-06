import { useSessionStore } from "../stores/useSessionStore";
import { SseParser } from "./sseParser";
import type {
  ChangeRecord,
  ChangeSetDecisionResult,
  Checkpoint,
  CommandDecisionResult,
  FileContent,
  FileWriteResult,
  LocalBrowseResponse,
  PatchDecisionResult,
  PermissionMode,
  Provider,
  ProviderTestResult,
  RunCreated,
  Session,
  SessionCreated,
  SessionDetail,
  SSEEvent,
} from "./types";
import { encodePath } from "./utils";

/** 后端基地址：默认本地 uvicorn；可用 VITE_API_BASE 覆盖（兼容旧名 VITE_API_BASE_URL）。 */
export const API_BASE = (
  import.meta.env.VITE_API_BASE ??
  import.meta.env.VITE_API_BASE_URL ??
  "http://localhost:8010"
).replace(/\/+$/, "");

export function apiUrl(path: string): string {
  return `${API_BASE}${path}`;
}

/** http:// → ws://，用于终端 WebSocket。 */
export function wsUrl(path: string): string {
  return `${API_BASE.replace(/^http/i, "ws")}${path}`;
}

export function encodeFilePath(path: string): string {
  return encodePath(path);
}

/** 根目录必须带末尾斜杠（后端 `{path:path}` 会因此匹配空串）。 */
export function fileUrl(sessionId: string, path: string): string {
  const encoded = encodePath(path);
  return encoded
    ? `/files/${sessionId}/${encoded}`
    : `/files/${sessionId}/`;
}

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export async function apiFetch<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const token = useSessionStore.getState().token;
  const headers = new Headers(init?.headers);
  if (init?.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const res = await fetch(apiUrl(path), { ...init, headers });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new ApiError(res.status, detail || `请求失败（HTTP ${res.status}）`);
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return (await res.json()) as T;
}

export function formatError(error: unknown): string {
  if (error instanceof ApiError) {
    try {
      const parsed = JSON.parse(error.message) as { detail?: string };
      if (parsed?.detail) return parsed.detail;
    } catch {
      // 非 JSON 错误体，直接使用原文
    }
    return error.message;
  }
  if (error instanceof Error) return error.message;
  return String(error);
}

// ---------------------------------------------------------------- typed client

export interface CreateSessionOptions {
  demoRepo?: string;
  /** 绝对路径；提供时创建本地目录会话（优先生效）。 */
  localPath?: string;
}

export function createSession(
  options: CreateSessionOptions = {},
): Promise<SessionCreated> {
  const localPath = options.localPath?.trim();
  const body = localPath
    ? { local_path: localPath }
    : { demo_repo: options.demoRepo ?? "todo-app" };
  return apiFetch<SessionCreated>("/sessions", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function getSession(sessionId: string): Promise<SessionDetail> {
  return apiFetch<SessionDetail>(`/sessions/${sessionId}`);
}

export function deleteSession(sessionId: string): Promise<{ ok: true }> {
  return apiFetch<{ ok: true }>(`/sessions/${sessionId}`, { method: "DELETE" });
}

export function runAgent(sessionId: string, message: string): Promise<RunCreated> {
  return apiFetch<RunCreated>(`/sessions/${sessionId}/run`, {
    method: "POST",
    body: JSON.stringify({ message }),
  });
}

/** 停止当前 run：后端投递 done(stopped=true) 后取消后台任务。 */
export function stopRun(
  sessionId: string,
): Promise<{ ok: boolean; stopped?: boolean; already_stopped?: boolean }> {
  return apiFetch(`/sessions/${sessionId}/stop`, { method: "POST" });
}

export function setPermissionMode(
  sessionId: string,
  mode: PermissionMode,
): Promise<Session> {
  return apiFetch<Session>(`/sessions/${sessionId}/preferences`, {
    method: "PATCH",
    body: JSON.stringify({ permission_mode: mode }),
  });
}

export function getFile(sessionId: string, path: string): Promise<FileContent> {
  return apiFetch<FileContent>(fileUrl(sessionId, path));
}

export function writeFile(
  sessionId: string,
  path: string,
  content: string,
): Promise<FileWriteResult> {
  return apiFetch<FileWriteResult>(fileUrl(sessionId, path), {
    method: "POST",
    body: JSON.stringify({ content }),
  });
}

export function terminalWsUrl(sessionId: string, token: string): string {
  return wsUrl(`/terminal/ws/${sessionId}?token=${encodeURIComponent(token)}`);
}

// ---------------------------------------------------------------- local dirs

export function getLocalHome(): Promise<{ path: string }> {
  return apiFetch<{ path: string }>("/local/home");
}

export function browseLocalDirs(path: string): Promise<LocalBrowseResponse> {
  return apiFetch<LocalBrowseResponse>(
    `/local/browse?path=${encodeURIComponent(path)}`,
  );
}

// ---------------------------------------------------------------- providers

export function listProviders(): Promise<Provider[]> {
  return apiFetch<Provider[]>("/providers");
}

export interface ProviderInput {
  name: string;
  base_url: string;
  model: string;
  api_key: string;
}

export function createProvider(input: ProviderInput): Promise<Provider> {
  return apiFetch<Provider>("/providers", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateProvider(
  id: string,
  input: Partial<ProviderInput>,
): Promise<Provider> {
  return apiFetch<Provider>(`/providers/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export function deleteProvider(id: string): Promise<{ ok: true; id: string }> {
  return apiFetch<{ ok: true; id: string }>(
    `/providers/${encodeURIComponent(id)}`,
    { method: "DELETE" },
  );
}

export function activateProvider(id: string): Promise<Provider> {
  return apiFetch<Provider>(`/providers/${encodeURIComponent(id)}/activate`, {
    method: "POST",
  });
}

export function testProvider(id: string): Promise<ProviderTestResult> {
  return apiFetch<ProviderTestResult>(
    `/providers/${encodeURIComponent(id)}/test`,
    { method: "POST" },
  );
}

/** 登记对 patch 的接受/拒绝决议（token 由 apiFetch 从 session store 读取）。 */
export function respondPatch(
  patchId: string,
  accepted: boolean,
): Promise<PatchDecisionResult> {
  return apiFetch<PatchDecisionResult>(
    `/agent/patch/${encodeURIComponent(patchId)}`,
    {
      method: "PATCH",
      body: JSON.stringify({ accepted }),
    },
  );
}

/** 提交多文件变更批次的逐文件决议（path -> 是否接受）。 */
export function respondChangeSet(
  changeSetId: string,
  decisions: Record<string, boolean>,
): Promise<ChangeSetDecisionResult> {
  return apiFetch<ChangeSetDecisionResult>(
    `/agent/change-set/${encodeURIComponent(changeSetId)}`,
    {
      method: "PATCH",
      body: JSON.stringify({ decisions }),
    },
  );
}

/** 登记对命令的接受/拒绝决议。 */
export function respondCommand(
  commandId: string,
  accepted: boolean,
): Promise<CommandDecisionResult> {
  return apiFetch<CommandDecisionResult>(
    `/agent/command/${encodeURIComponent(commandId)}`,
    {
      method: "PATCH",
      body: JSON.stringify({ accepted }),
    },
  );
}

/** 会话变更清单（Phase 6 的变更面板使用）。 */
export function listChanges(sessionId: string): Promise<ChangeRecord[]> {
  return apiFetch<ChangeRecord[]>(`/sessions/${encodeURIComponent(sessionId)}/changes`);
}

/** 会话检查点列表（后端最多返回最新 50 条，按时间倒序）。 */
export function listCheckpoints(sessionId: string): Promise<Checkpoint[]> {
  return apiFetch<Checkpoint[]>(
    `/sessions/${encodeURIComponent(sessionId)}/checkpoints`,
  );
}

/** 回滚到指定检查点之前：恢复快照，之后的改动与消息标记为 reverted。 */
export function rollbackCheckpoint(
  sessionId: string,
  checkpointId: string,
): Promise<{ ok: true; restored: number; checkpoint_id: string }> {
  return apiFetch(
    `/sessions/${encodeURIComponent(sessionId)}/checkpoints/${encodeURIComponent(checkpointId)}/rollback`,
    { method: "POST" },
  );
}

/**
 * fetch + ReadableStream 手写 SSE 解析（EventSource 无法携带 Authorization）。
 * 正常收到 done 返回 "done"；流提前结束时返回 "ended"。
 */
export async function streamEvents(
  sessionId: string,
  token: string,
  signal: AbortSignal,
  onEvent: (event: SSEEvent) => void,
): Promise<"done" | "ended"> {
  const res = await fetch(apiUrl(`/agent/stream/${sessionId}`), {
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "text/event-stream",
    },
    signal,
    cache: "no-store",
  });

  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new ApiError(
      res.status,
      detail || `SSE 连接失败（HTTP ${res.status}）`,
    );
  }
  if (!res.body) throw new Error("SSE 响应没有 body");

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  const parser = new SseParser();
  let doneSeen = false;

  const dispatch = (event: string, data: string): void => {
    try {
      const parsed: unknown = JSON.parse(data);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        return;
      }
      const payload = { type: event, ...(parsed as Record<string, unknown>) };
      if (event === "done") doneSeen = true;
      onEvent(payload as SSEEvent);
    } catch {
      // 非法 JSON：忽略该事件，保持流不中断
    }
  };

  const consume = (chunk: string): void => {
    for (const parsed of parser.feed(chunk)) {
      dispatch(parsed.event, parsed.data);
    }
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    consume(decoder.decode(value, { stream: true }));
  }

  const tail = decoder.decode();
  if (tail) consume(tail);
  for (const parsed of parser.flush()) {
    dispatch(parsed.event, parsed.data);
  }

  return doneSeen ? "done" : "ended";
}

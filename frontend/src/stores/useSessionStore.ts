import { create } from "zustand";

import {
  createSession as createSessionRequest,
  formatError,
  getSession,
  setPermissionMode as setPermissionModeRequest,
  type CreateSessionOptions,
} from "../lib/api";
import type { PermissionMode } from "../lib/types";
import { useChatStore } from "./useChatStore";
import { useEditorStore } from "./useEditorStore";
import { useWorkspaceStore } from "./useWorkspaceStore";

export type ContainerStatus = "green" | "yellow" | "red";

export const DEMO_REPOS = [
  "todo-app",
  "express-demo",
  "python-flask-demo",
] as const;

const TOKEN_KEY = "devflow_token";
const SESSION_KEY = "devflow_session_id";

/** sessionStorage 在 SSR / 测试环境可能不存在，统一做空值保护。 */
function safeStorage(): Storage | null {
  try {
    return typeof sessionStorage === "undefined" ? null : sessionStorage;
  } catch {
    return null;
  }
}

function persistSession(id: string, token: string): void {
  const storage = safeStorage();
  if (!storage) return;
  storage.setItem(SESSION_KEY, id);
  storage.setItem(TOKEN_KEY, token);
}

function clearPersistedSession(): void {
  const storage = safeStorage();
  if (!storage) return;
  storage.removeItem(SESSION_KEY);
  storage.removeItem(TOKEN_KEY);
}

interface SessionState {
  id: string | null;
  token: string | null;
  demoRepo: string;
  /** 当前本地目录会话的根路径；demo 会话为 null。 */
  localPath: string | null;
  containerStatus: ContainerStatus;
  /** 当前会话权限模式；服务端为准，前端切换后以返回值覆盖。 */
  permissionMode: PermissionMode;
  creating: boolean;
  error: string | null;
  setPermissionMode: (mode: PermissionMode) => Promise<void>;
  createSession: (options?: CreateSessionOptions) => Promise<boolean>;
  /** 用已有 id + token 恢复会话：拉历史消息 + 重建文件树。 */
  restoreSession: (id: string, token: string) => Promise<boolean>;
  /** 页面挂载入口：sessionStorage 有凭据则恢复，否则新建会话。 */
  bootstrap: () => Promise<boolean>;
  setDemoRepo: (repo: string) => void;
  setContainerStatus: (status: ContainerStatus) => void;
  /** 重新拉取根目录以探测 / 恢复沙箱状态（失败置 red）。 */
  refreshSandbox: () => Promise<void>;
  reset: () => void;
}

export const useSessionStore = create<SessionState>((set, get) => ({
  id: null,
  token: null,
  demoRepo: "todo-app",
  localPath: null,
  containerStatus: "yellow",
  permissionMode: "approval",
  creating: false,
  error: null,

  setDemoRepo: (repo) => set({ demoRepo: repo }),
  setContainerStatus: (status) => set({ containerStatus: status }),

  createSession: async (options) => {
    const localPath = options?.localPath?.trim() || null;
    const repo = options?.demoRepo ?? get().demoRepo;
    set({
      id: null,
      token: null,
      demoRepo: repo,
      localPath,
      creating: true,
      containerStatus: "yellow",
      error: null,
    });

    try {
      const created = await createSessionRequest(
        localPath ? { localPath } : { demoRepo: repo },
      );

      useChatStore.getState().clear();
      useEditorStore.getState().reset();
      useWorkspaceStore.getState().reset();

      set({
        id: created.id,
        token: created.token,
        demoRepo: created.demo_repo,
        localPath: created.local_path ?? localPath,
        permissionMode: created.permission_mode ?? "approval",
        creating: false,
      });
      persistSession(created.id, created.token);

      // 立即请求根目录，触发 workspace 懒初始化；成功即沙箱就绪（green）。
      await get().refreshSandbox();
      return true;
    } catch (error) {
      set({
        creating: false,
        containerStatus: "red",
        error: formatError(error),
      });
      return false;
    }
  },

  restoreSession: async (id, token) => {
    set({ id, token, creating: true, containerStatus: "yellow", error: null });
    try {
      const detail = await getSession(id);
      useChatStore.getState().loadHistory(detail.messages);
      useChatStore.getState().setPlan(detail.plan ?? null);
      set({
        demoRepo: detail.demo_repo,
        localPath: detail.local_path ?? null,
        permissionMode: detail.permission_mode ?? "approval",
        creating: false,
        containerStatus: detail.container_ready ? "green" : "yellow",
      });
      persistSession(id, token);
      await get().refreshSandbox();
      return true;
    } catch (error) {
      set({
        creating: false,
        containerStatus: "red",
        error: formatError(error),
      });
      return false;
    }
  },

  bootstrap: async () => {
    const storage = safeStorage();
    const id = storage?.getItem(SESSION_KEY) ?? null;
    const token = storage?.getItem(TOKEN_KEY) ?? null;

    if (id && token) {
      const restored = await get().restoreSession(id, token);
      if (restored) return true;
      // 凭据失效（过期/被删）：清掉后重新建会话
      clearPersistedSession();
    }
    return get().createSession();
  },

  setPermissionMode: async (mode) => {
    const sessionId = get().id;
    if (!sessionId) {
      set({ permissionMode: mode });
      return;
    }
    const previous = get().permissionMode;
    set({ permissionMode: mode });
    try {
      const updated = await setPermissionModeRequest(sessionId, mode);
      set({ permissionMode: updated.permission_mode ?? mode });
    } catch (error) {
      set({ permissionMode: previous, error: formatError(error) });
    }
  },

  refreshSandbox: async () => {
    const sessionId = get().id;
    if (!sessionId) return;
    set({ containerStatus: "yellow" });
    try {
      await useWorkspaceStore.getState().loadTree(sessionId);
      set({ containerStatus: "green", error: null });
    } catch (error) {
      set({ containerStatus: "red", error: formatError(error) });
    }
  },

  reset: () => {
    clearPersistedSession();
    set({
      id: null,
      token: null,
      localPath: null,
      containerStatus: "yellow",
      permissionMode: "approval",
      creating: false,
      error: null,
    });
  },
}));

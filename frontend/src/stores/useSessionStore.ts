import { create } from "zustand";

import {
  createSession as createSessionRequest,
  formatError,
  getSession,
} from "../lib/api";
import { useChatStore } from "./useChatStore";
import { useEditorStore } from "./useEditorStore";
import { useWorkspaceStore } from "./useWorkspaceStore";

export type ContainerStatus = "green" | "yellow" | "red";

export const DEMO_REPOS = [
  "todo-app",
  "express-demo",
  "python-flask-demo",
] as const;

interface SessionState {
  id: string | null;
  /** 仅存内存（Zustand），刷新页面即丢失。 */
  token: string | null;
  demoRepo: string;
  containerStatus: ContainerStatus;
  creating: boolean;
  error: string | null;
  createSession: (demoRepo?: string) => Promise<boolean>;
  /** 用已有 id + token 恢复会话（Phase 5 持久化入口，当前仅重建内存态）。 */
  restoreSession: (id: string, token: string) => Promise<boolean>;
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
  containerStatus: "yellow",
  creating: false,
  error: null,

  setDemoRepo: (repo) => set({ demoRepo: repo }),
  setContainerStatus: (status) => set({ containerStatus: status }),

  createSession: async (demoRepo) => {
    const repo = demoRepo ?? get().demoRepo;
    set({
      id: null,
      token: null,
      demoRepo: repo,
      creating: true,
      containerStatus: "yellow",
      error: null,
    });

    try {
      const created = await createSessionRequest(repo);

      useChatStore.getState().clear();
      useEditorStore.getState().reset();
      useWorkspaceStore.getState().reset();

      set({
        id: created.id,
        token: created.token,
        demoRepo: created.demo_repo,
        creating: false,
      });

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
      set({
        demoRepo: detail.demo_repo,
        creating: false,
        containerStatus: detail.container_ready ? "green" : "yellow",
      });
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

  reset: () =>
    set({
      id: null,
      token: null,
      containerStatus: "yellow",
      creating: false,
      error: null,
    }),
}));

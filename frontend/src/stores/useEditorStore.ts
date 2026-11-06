import { create } from "zustand";

import { formatError, getFile, writeFile } from "../lib/api";
import { useSessionStore } from "./useSessionStore";

const SAVE_DEBOUNCE_MS = 300;
const MAX_TABS = 10;
const STORAGE_KEY = "devflow_editor_tabs";
const AGENT_HIGHLIGHT_MS = 1200;

/** agent 写入/删除造成的内容冲突；agentContent=null 表示文件已被删除。 */
export interface EditorConflict {
  agentContent: string | null;
}

export interface EditorTab {
  path: string;
  /** 编辑器当前内容（用户可编辑）。 */
  content: string;
  /** 最近一次从服务端读到的内容，用于冲突时对照。 */
  serverContent: string;
  dirty: boolean;
  saving: boolean;
  loading: boolean;
  truncated: boolean;
  error: string | null;
  conflict: EditorConflict | null;
  /** agent 刚修改过，短暂高亮提示。 */
  agentUpdated: boolean;
  lastUsed: number;
}

const saveTimers = new Map<string, number>();
const highlightTimers = new Map<string, number>();

function safeStorage(): Storage | null {
  try {
    return typeof sessionStorage === "undefined" ? null : sessionStorage;
  } catch {
    return null;
  }
}

function clearSaveTimer(path: string): void {
  const timer = saveTimers.get(path);
  if (timer !== undefined) {
    window.clearTimeout(timer);
    saveTimers.delete(path);
  }
}

function clearHighlightTimer(path: string): void {
  const timer = highlightTimers.get(path);
  if (timer !== undefined) {
    window.clearTimeout(timer);
    highlightTimers.delete(path);
  }
}

function clearAllTimers(): void {
  saveTimers.forEach((timer) => window.clearTimeout(timer));
  saveTimers.clear();
  highlightTimers.forEach((timer) => window.clearTimeout(timer));
  highlightTimers.clear();
}

interface PersistedTabs {
  sessionId: string;
  paths: string[];
  activePath: string | null;
}

function readPersistedTabs(): PersistedTabs | null {
  const storage = safeStorage();
  if (!storage) return null;
  try {
    const raw = storage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<PersistedTabs>;
    if (!parsed || typeof parsed.sessionId !== "string") return null;
    const paths = Array.isArray(parsed.paths)
      ? parsed.paths.filter((path): path is string => typeof path === "string")
      : [];
    return {
      sessionId: parsed.sessionId,
      paths: paths.slice(0, MAX_TABS),
      activePath:
        typeof parsed.activePath === "string" ? parsed.activePath : null,
    };
  } catch {
    return null;
  }
}

function writePersistedTabs(state: {
  tabs: EditorTab[];
  activePath: string | null;
}): void {
  const storage = safeStorage();
  if (!storage) return;
  const sessionId = useSessionStore.getState().id;
  if (!sessionId) return;
  const payload: PersistedTabs = {
    sessionId,
    paths: state.tabs.map((tab) => tab.path),
    activePath: state.activePath,
  };
  try {
    storage.setItem(STORAGE_KEY, JSON.stringify(payload));
  } catch {
    // 存储不可用（隐私模式/配额）时不影响编辑
  }
}

function removePersistedTabs(): void {
  try {
    safeStorage()?.removeItem(STORAGE_KEY);
  } catch {
    // 忽略存储异常
  }
}

interface EditorState {
  tabs: EditorTab[];
  activePath: string | null;
  open: (sessionId: string, path: string) => Promise<void>;
  activate: (path: string) => void;
  handleChange: (path: string, content: string) => void;
  save: (path?: string) => Promise<void>;
  closeTab: (path: string) => void;
  resolveConflict: (path: string, choice: "mine" | "agent") => void;
  handleFileChanged: (
    sessionId: string,
    path: string,
    operation: "write" | "delete" | "rollback",
  ) => Promise<void>;
  reloadTab: (sessionId: string, path: string) => Promise<void>;
  /** 回滚后刷新所有非 dirty 标签，dirty 的转为冲突。 */
  refreshAll: (sessionId: string) => Promise<void>;
  restoreTabs: (sessionId: string) => Promise<void>;
  reset: () => void;
}

export const useEditorStore = create<EditorState>((set, get) => {
  function updateTab(path: string, updater: (tab: EditorTab) => EditorTab): void {
    set((state) => ({
      tabs: state.tabs.map((tab) => (tab.path === path ? updater(tab) : tab)),
    }));
  }

  function markAgentUpdated(path: string): void {
    clearHighlightTimer(path);
    updateTab(path, (tab) => ({ ...tab, agentUpdated: true }));
    highlightTimers.set(
      path,
      window.setTimeout(() => {
        highlightTimers.delete(path);
        if (get().tabs.some((tab) => tab.path === path)) {
          updateTab(path, (tab) => ({ ...tab, agentUpdated: false }));
        }
      }, AGENT_HIGHLIGHT_MS),
    );
  }

  async function loadInto(
    sessionId: string,
    path: string,
    options: { highlight: boolean },
  ): Promise<void> {
    updateTab(path, (tab) => ({ ...tab, loading: true, error: null }));
    try {
      const data = await getFile(sessionId, path);
      if (data.type !== "file") {
        get().closeTab(path);
        return;
      }
      const content = data.content ?? "";
      updateTab(path, (tab) => {
        // 拉取期间用户可能已输入：保留用户内容并标记冲突
        if (tab.dirty) {
          return {
            ...tab,
            loading: false,
            conflict: { agentContent: content },
          };
        }
        return {
          ...tab,
          content,
          serverContent: content,
          dirty: false,
          loading: false,
          truncated: Boolean(data.truncated),
          error: null,
          conflict: null,
        };
      });
      if (options.highlight) markAgentUpdated(path);
    } catch (error) {
      updateTab(path, (tab) => ({
        ...tab,
        loading: false,
        error: formatError(error),
      }));
    }
  }

  async function persist(path: string): Promise<void> {
    const sessionId = useSessionStore.getState().id;
    const tab = get().tabs.find((item) => item.path === path);
    if (!sessionId || !tab) return;
    const snapshot = tab.content;
    updateTab(path, (item) => ({ ...item, saving: true, error: null }));
    try {
      await writeFile(sessionId, path, snapshot);
      updateTab(path, (item) =>
        item.path === path
          ? {
              ...item,
              saving: false,
              serverContent: snapshot,
              dirty: item.content !== snapshot,
              error: null,
            }
          : item,
      );
    } catch (error) {
      updateTab(path, (item) => ({
        ...item,
        saving: false,
        error: formatError(error),
      }));
    }
  }

  function scheduleSave(sessionId: string, path: string): void {
    clearSaveTimer(path);
    saveTimers.set(
      path,
      window.setTimeout(() => {
        saveTimers.delete(path);
        const tab = get().tabs.find((item) => item.path === path);
        if (!tab || !tab.dirty) return;
        if (useSessionStore.getState().id !== sessionId) return;
        void persist(path);
      }, SAVE_DEBOUNCE_MS),
    );
  }

  function persistTabs(): void {
    writePersistedTabs({ tabs: get().tabs, activePath: get().activePath });
  }

  return {
    tabs: [],
    activePath: null,

    open: async (sessionId, path) => {
      const existing = get().tabs.find((tab) => tab.path === path);
      if (existing) {
        set((state) => ({
          activePath: path,
          tabs: state.tabs.map((tab) =>
            tab.path === path ? { ...tab, lastUsed: Date.now() } : tab,
          ),
        }));
        persistTabs();
        return;
      }

      const tab: EditorTab = {
        path,
        content: "",
        serverContent: "",
        dirty: false,
        saving: false,
        loading: true,
        truncated: false,
        error: null,
        conflict: null,
        agentUpdated: false,
        lastUsed: Date.now(),
      };

      set((state) => {
        const tabs = [...state.tabs, tab];
        let trimmed = tabs;
        if (tabs.length > MAX_TABS) {
          // 超过上限时淘汰最久未使用、非活动且无未保存内容的标签
          const removable = tabs
            .filter(
              (item) => item.path !== path && item.path !== state.activePath && !item.dirty,
            )
            .sort((a, b) => a.lastUsed - b.lastUsed);
          if (removable.length > 0) {
            const dropPath = removable[0].path;
            clearSaveTimer(dropPath);
            clearHighlightTimer(dropPath);
            trimmed = tabs.filter((item) => item.path !== dropPath);
          }
        }
        return { tabs: trimmed, activePath: path };
      });
      persistTabs();

      await loadInto(sessionId, path, { highlight: false });
    },

    activate: (path) => {
      if (!get().tabs.some((tab) => tab.path === path)) return;
      set((state) => ({
        activePath: path,
        tabs: state.tabs.map((tab) =>
          tab.path === path ? { ...tab, lastUsed: Date.now() } : tab,
        ),
      }));
      persistTabs();
    },

    handleChange: (path, content) => {
      const tab = get().tabs.find((item) => item.path === path);
      if (!tab) return;
      updateTab(path, (item) => ({
        ...item,
        content,
        dirty: true,
        lastUsed: Date.now(),
      }));

      const sessionId = useSessionStore.getState().id;
      if (!sessionId) return;
      scheduleSave(sessionId, path);
    },

    save: async (pathArg) => {
      const path = pathArg ?? get().activePath;
      if (!path) return;
      clearSaveTimer(path);
      await persist(path);
    },

    closeTab: (path) => {
      clearSaveTimer(path);
      clearHighlightTimer(path);
      set((state) => {
        const index = state.tabs.findIndex((tab) => tab.path === path);
        if (index === -1) return state;
        const tabs = state.tabs.filter((tab) => tab.path !== path);
        let activePath = state.activePath;
        if (activePath === path) {
          const neighbor = tabs[index] ?? tabs[index - 1] ?? null;
          activePath = neighbor?.path ?? null;
        }
        return { tabs, activePath };
      });
      persistTabs();
    },

    resolveConflict: (path, choice) => {
      const tab = get().tabs.find((item) => item.path === path);
      if (!tab?.conflict) return;

      if (choice === "agent") {
        const agentContent = tab.conflict.agentContent;
        if (agentContent === null) {
          get().closeTab(path);
          return;
        }
        updateTab(path, (item) => ({
          ...item,
          content: agentContent,
          serverContent: agentContent,
          dirty: false,
          conflict: null,
          error: null,
        }));
        markAgentUpdated(path);
        persistTabs();
        return;
      }

      // 保留我的：清除冲突并立即保存，用用户内容覆盖 agent 版本
      updateTab(path, (item) => ({
        ...item,
        conflict: null,
        dirty: true,
      }));
      const sessionId = useSessionStore.getState().id;
      if (sessionId) scheduleSave(sessionId, path);
    },

    handleFileChanged: async (sessionId, path, operation) => {
      const tab = get().tabs.find((item) => item.path === path);
      if (!tab) return;

      if (operation === "delete") {
        if (tab.dirty) {
          updateTab(path, (item) => ({
            ...item,
            conflict: { agentContent: null },
          }));
        } else {
          get().closeTab(path);
        }
        return;
      }

      if (tab.dirty) {
        // 不覆盖用户编辑，拉取 agent 版本供用户三选一
        try {
          const data = await getFile(sessionId, path);
          updateTab(path, (item) => ({
            ...item,
            conflict: { agentContent: data.content ?? "" },
          }));
        } catch (error) {
          updateTab(path, (item) => ({
            ...item,
            error: formatError(error),
          }));
        }
        return;
      }

      await loadInto(sessionId, path, { highlight: true });
    },

    reloadTab: async (sessionId, path) => {
      if (!get().tabs.some((tab) => tab.path === path)) return;
      await loadInto(sessionId, path, { highlight: true });
    },

    refreshAll: async (sessionId) => {
      const paths = get().tabs.map((tab) => tab.path);
      await Promise.all(
        paths.map((path) => get().handleFileChanged(sessionId, path, "write")),
      );
    },

    restoreTabs: async (sessionId) => {
      const stored = readPersistedTabs();
      if (!stored || stored.sessionId !== sessionId) return;
      const paths = stored.paths.slice(0, MAX_TABS);
      if (paths.length === 0) return;

      for (const path of paths) {
        await get().open(sessionId, path);
      }
      if (stored.activePath && paths.includes(stored.activePath)) {
        get().activate(stored.activePath);
      }
    },

    reset: () => {
      clearAllTimers();
      removePersistedTabs();
      set({ tabs: [], activePath: null });
    },
  };
});

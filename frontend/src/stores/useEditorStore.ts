import { create } from "zustand";

import { formatError, getFile, writeFile } from "../lib/api";
import { useSessionStore } from "./useSessionStore";

const SAVE_DEBOUNCE_MS = 300;

let saveTimer: number | null = null;

function clearSaveTimer(): void {
  if (saveTimer !== null) {
    window.clearTimeout(saveTimer);
    saveTimer = null;
  }
}

interface EditorState {
  filePath: string | null;
  content: string;
  dirty: boolean;
  saving: boolean;
  loading: boolean;
  truncated: boolean;
  error: string | null;
  open: (sessionId: string, path: string) => Promise<void>;
  handleChange: (content: string) => void;
  /** 立即保存当前文件（内部 autosave 也走这里）。 */
  save: () => Promise<void>;
  /** 关闭当前标签页（丢弃未提交的自动保存计时器）。 */
  close: () => void;
  reset: () => void;
}

export const useEditorStore = create<EditorState>((set, get) => {
  async function persist(
    sessionId: string,
    path: string,
    content: string,
  ): Promise<void> {
    set({ saving: true });
    try {
      await writeFile(sessionId, path, content);
      set((state) =>
        state.filePath === path
          ? {
              saving: false,
              error: null,
              dirty: state.content !== content,
            }
          : { saving: false },
      );
    } catch (error) {
      set({ saving: false, error: formatError(error) });
    }
  }

  return {
    filePath: null,
    content: "",
    dirty: false,
    saving: false,
    loading: false,
    truncated: false,
    error: null,

    open: async (sessionId, path) => {
      clearSaveTimer();
      set({
        filePath: path,
        content: "",
        dirty: false,
        loading: true,
        truncated: false,
        error: null,
      });
      try {
        const data = await getFile(sessionId, path);
        if (data.type !== "file") {
          set({ loading: false, filePath: null });
          return;
        }
        set({
          content: data.content ?? "",
          truncated: Boolean(data.truncated),
          loading: false,
          dirty: false,
        });
      } catch (error) {
        set({ loading: false, content: "", error: formatError(error) });
      }
    },

    handleChange: (content) => {
      const path = get().filePath;
      if (!path) return;
      set({ content, dirty: true });

      const sessionId = useSessionStore.getState().id;
      if (!sessionId) return;

      clearSaveTimer();
      saveTimer = window.setTimeout(() => {
        saveTimer = null;
        // 防抖窗口内切换文件时不再写旧路径
        if (get().filePath !== path) return;
        void persist(sessionId, path, get().content);
      }, SAVE_DEBOUNCE_MS);
    },

    save: async () => {
      const sessionId = useSessionStore.getState().id;
      const path = get().filePath;
      if (!sessionId || !path) return;
      clearSaveTimer();
      await persist(sessionId, path, get().content);
    },

    close: () => {
      clearSaveTimer();
      set({
        filePath: null,
        content: "",
        dirty: false,
        saving: false,
        loading: false,
        truncated: false,
        error: null,
      });
    },

    reset: () => {
      clearSaveTimer();
      set({
        filePath: null,
        content: "",
        dirty: false,
        saving: false,
        loading: false,
        truncated: false,
        error: null,
      });
    },
  };
});

import { create } from "zustand";

import { formatError, getFile } from "../lib/api";
import type { FileEntry } from "../lib/types";

export interface FileNode {
  name: string;
  path: string;
  type: "dir" | "file";
  size: number;
  children?: FileNode[];
}

const MAX_DEPTH = 6;
const EXCLUDED_DIRS = new Set([
  "node_modules",
  "dist",
  "build",
  "coverage",
  "target",
  "__pycache__",
  ".git",
]);

async function fetchEntries(
  sessionId: string,
  path: string,
): Promise<FileEntry[]> {
  const data = await getFile(sessionId, path);
  if (data.type !== "dir" || !data.entries) {
    throw new Error(`不是目录：${path || "/"}`);
  }
  return data.entries;
}

async function buildNodes(
  sessionId: string,
  path: string,
  depth: number,
): Promise<FileNode[]> {
  const entries = (await fetchEntries(sessionId, path))
    .filter(
      (entry) => !entry.name.startsWith(".") && !EXCLUDED_DIRS.has(entry.name),
    )
    .sort((a, b) => {
      if (a.type !== b.type) return a.type === "dir" ? -1 : 1;
      return a.name.localeCompare(b.name, undefined, { sensitivity: "base" });
    });

  return Promise.all(
    entries.map(async (entry) => {
      const node: FileNode = {
        name: entry.name,
        path: entry.path,
        type: entry.type,
        size: entry.size,
      };
      if (entry.type === "dir" && depth < MAX_DEPTH) {
        node.children = await buildNodes(sessionId, entry.path, depth + 1).catch(
          () => [],
        );
      }
      return node;
    }),
  );
}

interface WorkspaceState {
  nodes: FileNode[];
  loading: boolean;
  loadedFor: string | null;
  error: string | null;
  loadTree: (sessionId: string) => Promise<void>;
  reset: () => void;
}

export const useWorkspaceStore = create<WorkspaceState>((set) => ({
  nodes: [],
  loading: false,
  loadedFor: null,
  error: null,

  loadTree: async (sessionId) => {
    set({ loading: true, error: null });
    try {
      const nodes = await buildNodes(sessionId, "", 0);
      set({ nodes, loading: false, loadedFor: sessionId });
    } catch (error) {
      const message = formatError(error);
      set({ nodes: [], loading: false, error: message, loadedFor: null });
      throw error;
    }
  },

  reset: () => set({ nodes: [], loading: false, loadedFor: null, error: null }),
}));

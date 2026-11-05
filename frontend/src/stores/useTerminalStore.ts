import { create } from "zustand";

export type TerminalConnection = "idle" | "connecting" | "open" | "closed";

const MAX_LINES = 2000;

interface TerminalState {
  lines: string[];
  status: TerminalConnection;
  append: (chunk: string) => void;
  setStatus: (status: TerminalConnection) => void;
  clear: () => void;
}

export const useTerminalStore = create<TerminalState>((set) => ({
  lines: [],
  status: "idle",

  append: (chunk) =>
    set((state) => {
      const next = state.lines.concat(chunk.split(/\r?\n/).filter(Boolean));
      return { lines: next.slice(-MAX_LINES) };
    }),

  setStatus: (status) => set({ status }),

  clear: () => set({ lines: [] }),
}));

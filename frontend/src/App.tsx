import { useEffect, useRef } from "react";

import { ChatPanel } from "./components/ChatPanel";
import { FileTree } from "./components/FileTree";
import { MonacoEditor } from "./components/MonacoEditor";
import { TopBar } from "./components/TopBar";
import { TerminalPanel } from "./components/terminal/TerminalPanel";
import { useSSE } from "./lib/sse";
import { useSessionStore } from "./stores/useSessionStore";

export default function App() {
  const sessionId = useSessionStore((state) => state.id);
  const createSession = useSessionStore((state) => state.createSession);

  const createRef = useRef(createSession);
  createRef.current = createSession;
  const bootstrapped = useRef(false);

  // 首次进入自动建会话；后续由顶栏「新会话」/repo 切换触发
  useEffect(() => {
    if (bootstrapped.current) return;
    bootstrapped.current = true;
    if (!useSessionStore.getState().id) {
      void createRef.current();
    }
  }, []);

  useSSE(sessionId);

  const retry = () => {
    const state = useSessionStore.getState();
    if (state.id) {
      void state.refreshSandbox();
    } else {
      void createRef.current();
    }
  };

  return (
    <div className="flex h-screen w-screen flex-col overflow-hidden bg-bg text-text">
      <TopBar onRetry={retry} />

      <div className="flex min-h-0 flex-1 overflow-hidden">
        <FileTree />

        <main className="flex min-w-0 flex-1 flex-col overflow-hidden">
          <MonacoEditor />
          <TerminalPanel className="h-[200px]" />
        </main>

        <ChatPanel />
      </div>
    </div>
  );
}

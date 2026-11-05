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
  const bootstrap = useSessionStore((state) => state.bootstrap);

  const bootstrapRef = useRef(bootstrap);
  bootstrapRef.current = bootstrap;
  const bootstrapped = useRef(false);

  // 首次进入：有 sessionStorage 凭据则恢复会话（含历史消息），否则新建。
  useEffect(() => {
    if (bootstrapped.current) return;
    bootstrapped.current = true;
    if (!useSessionStore.getState().id) {
      void bootstrapRef.current();
    }
  }, []);

  useSSE(sessionId);

  const retry = () => {
    const state = useSessionStore.getState();
    if (state.id) {
      void state.refreshSandbox();
    } else {
      void bootstrapRef.current();
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

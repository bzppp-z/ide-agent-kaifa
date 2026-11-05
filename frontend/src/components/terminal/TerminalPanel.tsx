import { useEffect, useRef } from "react";
import { FitAddon } from "@xterm/addon-fit";
import { Terminal as XTerm } from "@xterm/xterm";

import { cn } from "../../lib/utils";
import { useTerminal } from "../../hooks/useTerminal";
import { useSessionStore } from "../../stores/useSessionStore";
import { Spinner } from "../ui/spinner";

const PROMPT = "\u001b[32mPS\u001b[0m> ";

export function TerminalPanel({ className }: { className?: string }) {
  const sessionId = useSessionStore((state) => state.id);
  const containerStatus = useSessionStore((state) => state.containerStatus);

  const hostRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<XTerm | null>(null);
  const lineRef = useRef("");
  const readyRef = useRef(false);

  const { status, sendCommand } = useTerminal(sessionId, (data) => {
    termRef.current?.write(data);
  });

  const sendRef = useRef(sendCommand);
  sendRef.current = sendCommand;

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    const term = new XTerm({
      fontFamily: '"JetBrains Mono", "Cascadia Code", Consolas, monospace',
      fontSize: 13,
      theme: {
        background: "#0B0D0E",
        foreground: "#E6E9EC",
        cursor: "#0FDC78",
        cursorAccent: "#0B0D0E",
        selectionBackground: "#0FDC7833",
        black: "#0B0D0E",
        brightBlack: "#6E7681",
        red: "#F87171",
        green: "#0FDC78",
        yellow: "#FACC15",
        blue: "#60A5FA",
        magenta: "#C084FC",
        cyan: "#22D3EE",
        white: "#E6E9EC",
      },
      cursorBlink: true,
      convertEol: true,
      scrollback: 5000,
    });

    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(host);
    termRef.current = term;

    // 整行缓冲：后端把每条 WS 消息当成一条命令（自动补 \n），不能逐字符发送。
    const disposable = term.onData((data) => {
      if (data === "\r") {
        const command = lineRef.current;
        lineRef.current = "";
        term.write("\r\n");
        if (command.trim()) sendRef.current(command);
        return;
      }
      if (data === "\u007F") {
        if (lineRef.current.length > 0) {
          lineRef.current = lineRef.current.slice(0, -1);
          term.write("\b \b");
        }
        return;
      }
      if (data === "\u0003") {
        lineRef.current = "";
        term.write("^C\r\n");
        term.write(PROMPT);
        return;
      }
      // 过滤方向键等转义序列，只回显可打印字符
      if (data.charCodeAt(0) < 32 && data !== "\t") return;
      lineRef.current += data;
      term.write(data);
    });

    const safeFit = () => {
      try {
        fit.fit();
      } catch {
        // 容器尚未完成布局时 fit 会抛错，忽略即可
      }
    };
    safeFit();

    const observer = new ResizeObserver(safeFit);
    observer.observe(host);

    return () => {
      observer.disconnect();
      disposable.dispose();
      term.dispose();
      termRef.current = null;
      readyRef.current = false;
    };
  }, []);

  useEffect(() => {
    lineRef.current = "";
    termRef.current?.clear();
    readyRef.current = false;
  }, [sessionId]);

  useEffect(() => {
    if (status === "open" && !readyRef.current) {
      readyRef.current = true;
      termRef.current?.write(`\r\nDevFlow 终端已连接\r\n${PROMPT}`);
    }
    if (status !== "open") readyRef.current = false;
  }, [status]);

  const waiting = !sessionId || containerStatus === "red";

  return (
    <section
      className={cn(
        "flex shrink-0 flex-col border-t border-surface-border bg-bg",
        className,
      )}
    >
      <header className="flex h-7 shrink-0 items-center gap-2 border-b border-surface-border bg-surface px-3">
        <span className="text-[10.5px] uppercase tracking-wider text-muted-dim">
          终端
        </span>
        <span className="font-mono text-[10.5px] text-muted-dim">powershell</span>

        <span className="ml-auto flex items-center gap-1.5 text-[10.5px]">
          {status === "open" ? (
            <>
              <span className="h-1.5 w-1.5 rounded-full bg-brand" />
              <span className="text-muted">已连接</span>
            </>
          ) : status === "connecting" ? (
            <>
              <Spinner size={9} />
              <span className="text-yellow-400/90">连接中…</span>
            </>
          ) : status === "closed" ? (
            <>
              <span className="h-1.5 w-1.5 rounded-full bg-red-500" />
              <span className="text-red-400/90">已断开，重连中…</span>
            </>
          ) : (
            <span className="text-muted-dim">未连接</span>
          )}
        </span>
      </header>

      <div
        className="relative min-h-0 flex-1"
        onMouseDown={() => termRef.current?.focus()}
      >
        <div ref={hostRef} className="absolute inset-0 px-2 py-1" />

        {waiting ? (
          <div className="absolute inset-0 flex items-center justify-center gap-2 bg-bg text-[12px] text-muted-dim">
            {containerStatus === "red" ? (
              "沙箱异常，请在上方重试"
            ) : (
              <>
                <Spinner size={11} />
                等待沙箱启动…
              </>
            )}
          </div>
        ) : null}
      </div>
    </section>
  );
}

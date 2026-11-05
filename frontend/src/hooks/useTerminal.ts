import { useCallback, useEffect, useRef, useState } from "react";

import { terminalWsUrl } from "../lib/api";
import { useSessionStore } from "../stores/useSessionStore";
import {
  useTerminalStore,
  type TerminalConnection,
} from "../stores/useTerminalStore";

const RETRY_DELAYS_MS = [1000, 2000, 4000, 8000] as const;
/** 连续失败达到该次数后，顶栏沙箱状态置 red。 */
const RED_AFTER_FAILURES = 3;

export interface TerminalHandle {
  status: TerminalConnection;
  /** 发送完整一条命令（服务端会自动补换行）。 */
  sendCommand: (command: string) => void;
}

/**
 * 终端 WebSocket：token 走 query（WebSocket 不能带自定义 header）。
 * 后端按「一条消息 = 一条命令」处理（会自动补 \n），因此前端必须整行发送。
 * 断开后按 1s / 2s / 4s / 8s 指数退避重连。
 */
export function useTerminal(
  sessionId: string | null,
  onOutput?: (data: string) => void,
): TerminalHandle {
  const [status, setStatus] = useState<TerminalConnection>("idle");
  const socketRef = useRef<WebSocket | null>(null);
  const onOutputRef = useRef(onOutput);
  onOutputRef.current = onOutput;

  useEffect(() => {
    if (!sessionId) {
      setStatus("idle");
      useTerminalStore.getState().setStatus("idle");
      return;
    }

    let cancelled = false;
    let attempt = 0;
    let retryTimer: number | null = null;

    const update = (next: TerminalConnection) => {
      setStatus(next);
      useTerminalStore.getState().setStatus(next);
    };

    function connect(): void {
      if (cancelled) return;

      const token = useSessionStore.getState().token;
      if (!token) {
        retryTimer = window.setTimeout(connect, RETRY_DELAYS_MS[0]);
        return;
      }

      update("connecting");

      let socket: WebSocket;
      try {
        socket = new WebSocket(terminalWsUrl(sessionId!, token));
      } catch {
        update("closed");
        retryTimer = window.setTimeout(connect, RETRY_DELAYS_MS[1]);
        return;
      }

      socketRef.current = socket;

      socket.onopen = () => {
        if (cancelled) return;
        attempt = 0;
        update("open");
        // WS 就绪说明沙箱可用 → 顶栏状态同步为 green
        useSessionStore.getState().setContainerStatus("green");
      };

      socket.onmessage = (event: MessageEvent) => {
        if (typeof event.data !== "string") return;
        useTerminalStore.getState().append(event.data);
        onOutputRef.current?.(event.data);
      };

      socket.onclose = () => {
        if (cancelled) return;
        update("closed");
        useSessionStore
          .getState()
          .setContainerStatus(attempt >= RED_AFTER_FAILURES ? "red" : "yellow");
        const delay =
          RETRY_DELAYS_MS[Math.min(attempt, RETRY_DELAYS_MS.length - 1)];
        attempt += 1;
        retryTimer = window.setTimeout(connect, delay);
      };

      socket.onerror = () => {
        // 关闭事件会随后触发，重连逻辑统一放在 onclose
      };
    }

    connect();

    return () => {
      cancelled = true;
      if (retryTimer !== null) window.clearTimeout(retryTimer);
      const socket = socketRef.current;
      socketRef.current = null;
      socket?.close();
      useTerminalStore.getState().setStatus("idle");
    };
  }, [sessionId]);

  const sendCommand = useCallback((command: string) => {
    const socket = socketRef.current;
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(command);
    }
  }, []);

  return { status, sendCommand };
}

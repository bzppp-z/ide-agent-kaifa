import { useEffect } from "react";

import { ApiError, streamEvents } from "./api";
import { useChatStore } from "../stores/useChatStore";
import { useSessionStore } from "../stores/useSessionStore";

/** 异常断线最多重试次数；超过后提示用户重新发送。 */
const MAX_RETRIES = 3;
const RETRY_DELAY_MS = 2000;

/**
 * SSE 流：fetch + ReadableStream 手动解析（EventSource 无法带 Authorization）。
 * 只在 chat store streaming=true 时连接；收到 done 结束，异常断开最多重试 3 次。
 * 「停止」按钮会 abort 当前 fetch。
 */
export function useSSE(sessionId: string | null): void {
  useEffect(() => {
    if (!sessionId) return;

    let cancelled = false;
    let activeController: AbortController | null = null;
    let retryTimer: number | null = null;

    const sleep = (ms: number) =>
      new Promise<void>((resolve) => {
        retryTimer = window.setTimeout(() => {
          retryTimer = null;
          resolve();
        }, ms);
      });

    const fail = (attempts: number): void => {
      useChatStore.getState().pushEvent({
        type: "error",
        message: `SSE 连接中断，已重试 ${attempts} 次仍失败，请重新发送消息。`,
        code: "sse_reconnect_exhausted",
      });
      useChatStore.getState().endStream();
    };

    const connectLoop = async (): Promise<void> => {
      let attempts = 0;

      while (!cancelled) {
        const chat = useChatStore.getState();
        if (!chat.streaming) {
          attempts = 0;
          await sleep(150);
          continue;
        }

        const token = useSessionStore.getState().token;
        if (!token) {
          await sleep(1000);
          continue;
        }

        const controller = new AbortController();
        activeController = controller;
        chat.setSseAbort(controller);

        try {
          const result = await streamEvents(
            sessionId,
            token,
            controller.signal,
            (event) => useChatStore.getState().pushEvent(event),
          );

          activeController = null;
          useChatStore.getState().setSseAbort(null);

          if (result === "done") {
            useChatStore.getState().endStream();
            attempts = 0;
            continue;
          }

          // 流自然结束但没有 done：视为异常断开，重试。
          attempts += 1;
          if (attempts > MAX_RETRIES) {
            fail(MAX_RETRIES);
            continue;
          }
          await sleep(RETRY_DELAY_MS);
        } catch (error) {
          activeController = null;
          useChatStore.getState().setSseAbort(null);

          if (cancelled) break;

          // 用户主动停止（stopStreaming 已 abort 并置 streaming=false）。
          if (controller.signal.aborted) continue;

          if (error instanceof ApiError && error.status === 401) {
            useChatStore.getState().pushEvent({
              type: "error",
              message: "SSE 连接鉴权失败，请新建会话",
              code: "sse_auth",
            });
            useChatStore.getState().endStream();
            continue;
          }

          if (error instanceof ApiError && error.status === 404) {
            if (attempts > 0) {
              // 重连时才出现的 404 = 异常断开后后端 run 已被取消
              fail(attempts);
            } else {
              // 没有活跃 run（已结束 / 尚未登记）：安静结束本地流式状态
              useChatStore.getState().endStream();
            }
            await sleep(300);
            continue;
          }

          attempts += 1;
          if (attempts > MAX_RETRIES) {
            fail(MAX_RETRIES);
            continue;
          }
          await sleep(RETRY_DELAY_MS);
        }
      }
    };

    void connectLoop();

    return () => {
      cancelled = true;
      if (retryTimer !== null) window.clearTimeout(retryTimer);
      activeController?.abort();
    };
  }, [sessionId]);
}

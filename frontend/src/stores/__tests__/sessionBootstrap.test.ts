import { beforeEach, describe, expect, it, vi } from "vitest";

import { useChatStore } from "../useChatStore";
import { useSessionStore } from "../useSessionStore";

const mocks = vi.hoisted(() => ({
  createSession: vi.fn(),
  getSession: vi.fn(),
  getFile: vi.fn(),
}));

vi.mock("../../lib/api", () => ({
  createSession: mocks.createSession,
  getSession: mocks.getSession,
  getFile: mocks.getFile,
  formatError: (error: unknown) =>
    error instanceof Error ? error.message : String(error),
  writeFile: vi.fn(),
}));

function makeStorage(): Storage {
  const map = new Map<string, string>();
  return {
    get length() {
      return map.size;
    },
    clear: () => map.clear(),
    getItem: (key: string) => map.get(key) ?? null,
    key: (index: number) => Array.from(map.keys())[index] ?? null,
    removeItem: (key: string) => void map.delete(key),
    setItem: (key: string, value: string) => void map.set(key, value),
  };
}

const CREATED = {
  id: "new-session",
  token: "new-token",
  demo_repo: "todo-app",
  created_at: "2026-11-05T10:00:00Z",
};

beforeEach(() => {
  vi.stubGlobal("sessionStorage", makeStorage());
  mocks.createSession.mockReset().mockResolvedValue(CREATED);
  mocks.getSession.mockReset();
  mocks.getFile.mockReset().mockResolvedValue({ type: "dir", entries: [] });
  useSessionStore.setState({
    id: null,
    token: null,
    demoRepo: "todo-app",
    containerStatus: "yellow",
    creating: false,
    error: null,
  });
});

describe("token / sessionId 持久化", () => {
  it("createSession 成功后写入 sessionStorage", async () => {
    await useSessionStore.getState().createSession();

    expect(sessionStorage.getItem("devflow_session_id")).toBe("new-session");
    expect(sessionStorage.getItem("devflow_token")).toBe("new-token");
  });

  it("reset 清空 sessionStorage", async () => {
    await useSessionStore.getState().createSession();
    useSessionStore.getState().reset();

    expect(sessionStorage.getItem("devflow_session_id")).toBeNull();
    expect(sessionStorage.getItem("devflow_token")).toBeNull();
  });
});

describe("bootstrap", () => {
  it("无持久化凭据时新建会话", async () => {
    const ok = await useSessionStore.getState().bootstrap();

    expect(ok).toBe(true);
    expect(mocks.createSession).toHaveBeenCalledTimes(1);
    expect(mocks.getSession).not.toHaveBeenCalled();
    expect(useSessionStore.getState().id).toBe("new-session");
  });

  it("有凭据时恢复会话并载入历史消息", async () => {
    sessionStorage.setItem("devflow_session_id", "s1");
    sessionStorage.setItem("devflow_token", "t1");
    mocks.getSession.mockResolvedValue({
      id: "s1",
      token: "t1",
      demo_repo: "todo-app",
      container_ready: true,
      created_at: "2026-11-05T10:00:00Z",
      messages: [
        {
          id: "m1",
          session_id: "s1",
          role: "user",
          content: "看看 src",
          created_at: "2026-11-05T10:00:00Z",
        },
        {
          id: "m2",
          session_id: "s1",
          role: "assistant",
          content: "已查看 src 目录",
          created_at: "2026-11-05T10:00:01Z",
        },
      ],
    });

    const ok = await useSessionStore.getState().bootstrap();

    expect(ok).toBe(true);
    expect(mocks.getSession).toHaveBeenCalledWith("s1");
    expect(useSessionStore.getState().id).toBe("s1");
    expect(useSessionStore.getState().token).toBe("t1");
    expect(useSessionStore.getState().containerStatus).toBe("green");

    const items = useChatStore.getState().items;
    expect(items).toHaveLength(2);
    expect(items[0]).toMatchObject({ kind: "user", content: "看看 src" });
    expect(items[1]).toMatchObject({ kind: "assistant" });
  });

  it("凭据失效时清空存储并回退到新建会话", async () => {
    sessionStorage.setItem("devflow_session_id", "stale");
    sessionStorage.setItem("devflow_token", "stale-token");
    mocks.getSession.mockRejectedValue(new Error("invalid token"));

    const ok = await useSessionStore.getState().bootstrap();

    expect(ok).toBe(true);
    expect(useSessionStore.getState().id).toBe("new-session");
    expect(sessionStorage.getItem("devflow_session_id")).toBe("new-session");
    expect(sessionStorage.getItem("devflow_token")).toBe("new-token");
  });
});

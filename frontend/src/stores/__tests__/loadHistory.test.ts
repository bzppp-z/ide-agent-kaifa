import { beforeEach, describe, expect, it } from "vitest";

import { useChatStore } from "../useChatStore";

function resetStore(): void {
  useChatStore.setState({
    items: [],
    streaming: false,
    runId: null,
    error: null,
  });
}

const base = { session_id: "s1", created_at: "2026-11-05T10:00:00Z" };

describe("chatStore.loadHistory（刷新恢复）", () => {
  beforeEach(resetStore);

  it("把 user/assistant 历史消息重建为气泡", () => {
    useChatStore.getState().loadHistory([
      { ...base, id: "m1", role: "user", content: "看看 src" },
      { ...base, id: "m2", role: "assistant", content: "已查看 src 目录" },
    ]);

    const items = useChatStore.getState().items;
    expect(items).toHaveLength(2);
    expect(items[0]).toMatchObject({ kind: "user", content: "看看 src" });
    expect(items[1]).toMatchObject({ kind: "assistant", content: "已查看 src 目录" });
  });

  it("忽略空 assistant 消息", () => {
    useChatStore.getState().loadHistory([
      { ...base, id: "m1", role: "user", content: "hi" },
      { ...base, id: "m2", role: "assistant", content: "   " },
    ]);

    expect(useChatStore.getState().items).toHaveLength(1);
  });

  it("把持久化的 patch.proposed JSON 还原成审批卡片", () => {
    const payload = JSON.stringify({
      type: "patch.proposed",
      patch_id: "p1",
      path: "src/index.js",
      search: "function listTodos() {",
      replace: "function health() {}",
      before: "function listTodos() {",
      after: "function health() {}",
      diff: "-function listTodos() {\n+function health() {}",
    });

    useChatStore.getState().loadHistory([
      { ...base, id: "m1", role: "user", content: "新增 health" },
      { ...base, id: "m2", role: "assistant", content: payload },
    ]);

    const patchItem = useChatStore.getState().items[1];
    expect(patchItem).toMatchObject({
      kind: "patch",
      patchId: "p1",
      path: "src/index.js",
      status: "pending",
      before: "function listTodos() {",
      after: "function health() {}",
    });
  });

  it("普通 JSON 文本不会被误判成 patch", () => {
    useChatStore.getState().loadHistory([
      { ...base, id: "m1", role: "assistant", content: '{"foo": 1}' },
    ]);

    expect(useChatStore.getState().items[0]).toMatchObject({ kind: "assistant" });
  });

  it("正在流式时不用历史覆盖当前气泡", () => {
    useChatStore.setState({
      items: [
        { id: "live", kind: "assistant", content: "生成中", streaming: true },
      ],
      streaming: true,
    });

    useChatStore.getState().loadHistory([
      { ...base, id: "m1", role: "user", content: "旧消息" },
    ]);

    const items = useChatStore.getState().items;
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({ id: "live" });
  });
});

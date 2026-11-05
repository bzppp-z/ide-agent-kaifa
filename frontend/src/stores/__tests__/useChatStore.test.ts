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

describe("useChatStore.pushEvent", () => {
  beforeEach(resetStore);

  it("连续 message.delta 合并到同一条 assistant 消息", () => {
    useChatStore.getState().pushEvent({ type: "message.delta", content: "你" });
    useChatStore.getState().pushEvent({ type: "message.delta", content: "好" });

    const items = useChatStore.getState().items;
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({
      kind: "assistant",
      content: "你好",
      streaming: true,
    });
  });

  it("tool.call 后 tool.result 更新同一张卡片", () => {
    useChatStore.getState().pushEvent({
      type: "tool.call",
      call_id: "c1",
      tool_name: "list",
      arguments: { path: "src" },
    });
    useChatStore.getState().pushEvent({
      type: "tool.result",
      call_id: "c1",
      tool_name: "list",
      ok: true,
      result: { summary: "3 entries" },
      duration_ms: 42,
    });

    const items = useChatStore.getState().items;
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({
      kind: "tool",
      toolName: "list",
      state: "ok",
      durationMs: 42,
    });
  });

  it("孤立 tool.result 也能生成一张失败卡片", () => {
    useChatStore.getState().pushEvent({
      type: "tool.result",
      call_id: "c2",
      tool_name: "read",
      ok: false,
      result: { summary: "not found" },
      duration_ms: 7,
    });

    const items = useChatStore.getState().items;
    expect(items[0]).toMatchObject({ kind: "tool", state: "error" });
  });

  it("done 事件终止流式并写入统计", () => {
    useChatStore.setState({ streaming: true });
    useChatStore.getState().pushEvent({ type: "message.delta", content: "hi" });
    useChatStore.getState().pushEvent({
      type: "done",
      run_id: "r1",
      warning: null,
      total_tokens: 99,
      total_steps: 3,
    });

    const state = useChatStore.getState();
    expect(state.streaming).toBe(false);
    expect(state.runId).toBeNull();
    expect(state.items[0]).toMatchObject({ kind: "assistant", streaming: false });
    expect(state.items[1]).toMatchObject({
      kind: "done",
      totalTokens: 99,
      totalSteps: 3,
    });
  });

  it("error 与 patch 事件都有对应条目", () => {
    useChatStore.getState().pushEvent({
      type: "error",
      message: "boom",
      code: "agent_error",
    });
    useChatStore.getState().pushEvent({
      type: "patch.proposed",
      patch_id: "p1",
      path: "src/index.js",
      search: "a",
      replace: "b",
    });

    const items = useChatStore.getState().items;
    expect(items[0]).toMatchObject({ kind: "error", message: "boom", code: "agent_error" });
    expect(items[1]).toMatchObject({ kind: "patch", patchId: "p1", path: "src/index.js" });
  });
});

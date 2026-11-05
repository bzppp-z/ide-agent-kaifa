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

describe("patch 状态映射", () => {
  beforeEach(resetStore);

  it("patch.proposed 事件生成待审批卡片并带上 before/after/diff", () => {
    useChatStore.getState().pushEvent({
      type: "patch.proposed",
      patch_id: "p1",
      path: "src/index.js",
      search: "function listTodos() {",
      replace: "function health() {}",
      before: "function listTodos() {",
      after: "function health() {}",
      diff: "-function listTodos() {\n+function health() {}",
    });

    const item = useChatStore.getState().items[0];
    expect(item).toMatchObject({
      kind: "patch",
      patchId: "p1",
      status: "pending",
      before: "function listTodos() {",
      after: "function health() {}",
    });
  });

  it("setPatchStatus 只更新对应 patch 的状态", () => {
    useChatStore.getState().pushEvent({
      type: "patch.proposed",
      patch_id: "p1",
      path: "a.js",
      search: "a",
      replace: "b",
    });
    useChatStore.getState().pushEvent({
      type: "patch.proposed",
      patch_id: "p2",
      path: "b.js",
      search: "c",
      replace: "d",
    });

    useChatStore.getState().setPatchStatus("p1", "accepted");

    const items = useChatStore.getState().items;
    expect(items[0]).toMatchObject({ patchId: "p1", status: "accepted" });
    expect(items[1]).toMatchObject({ patchId: "p2", status: "pending" });
  });

  it("setPatchStatus 可以显式覆写状态", () => {
    useChatStore.getState().pushEvent({
      type: "patch.proposed",
      patch_id: "p1",
      path: "a.js",
      search: "a",
      replace: "b",
    });

    useChatStore.getState().setPatchStatus("p1", "accepted");
    useChatStore.getState().setPatchStatus("p1", "rejected");

    const item = useChatStore.getState().items[0];
    expect(item).toMatchObject({ patchId: "p1", status: "rejected" });
  });
});

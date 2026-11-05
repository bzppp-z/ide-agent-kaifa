import { describe, expect, it } from "vitest";

import { buildPatchSides } from "../patchDiff";

describe("buildPatchSides", () => {
  it("从 unified diff 拆分删除行与新增行，并保留上下文", () => {
    const diff = [
      "--- a/src/index.js",
      "+++ b/src/index.js",
      "@@ -1,3 +1,5 @@",
      " const todos = [];",
      "-function listTodos() {",
      "+function health() {",
      "+  return { ok: true };",
      "+}",
      "+",
      " function listTodos() {",
    ].join("\n");

    const sides = buildPatchSides({ search: "", replace: "", diff });

    expect(sides.left).toEqual([
      "const todos = [];",
      "function listTodos() {",
      "function listTodos() {",
    ]);
    expect(sides.right).toEqual([
      "const todos = [];",
      "function health() {",
      "  return { ok: true };",
      "}",
      "",
      "function listTodos() {",
    ]);
  });

  it("diff 缺失时退回 search/replace 原文", () => {
    const sides = buildPatchSides({
      search: "const port = 3000;",
      replace: "const port = 8080;",
    });

    expect(sides).toEqual({
      left: ["const port = 3000;"],
      right: ["const port = 8080;"],
    });
  });

  it("diff 只有头部时也退回 search/replace", () => {
    const sides = buildPatchSides({
      search: "a",
      replace: "b",
      diff: "--- a/x\n+++ b/x\n@@ -1 +1 @@",
    });

    expect(sides).toEqual({ left: ["a"], right: ["b"] });
  });
});

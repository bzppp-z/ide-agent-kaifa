import { describe, expect, it } from "vitest";

import {
  compactPath,
  isAbsolutePath,
  sessionRootLabel,
} from "../utils";

describe("isAbsolutePath", () => {
  it("识别 Windows 盘符 / UNC / POSIX 根", () => {
    expect(isAbsolutePath("C:\\Users\\bzp20\\Desktop\\my-app")).toBe(true);
    expect(isAbsolutePath("D:/work/app")).toBe(true);
    expect(isAbsolutePath("\\\\server\\share")).toBe(true);
    expect(isAbsolutePath("/usr/local/app")).toBe(true);
  });

  it("拒绝空值与相对路径", () => {
    expect(isAbsolutePath("")).toBe(false);
    expect(isAbsolutePath("   ")).toBe(false);
    expect(isAbsolutePath("Desktop/my-app")).toBe(false);
    expect(isAbsolutePath("my-app")).toBe(false);
  });
});

describe("compactPath", () => {
  it("短路径原样返回", () => {
    expect(compactPath("C:\\Users\\bzp20")).toBe("C:\\Users\\bzp20");
  });

  it("长路径保留头尾并在中间省略", () => {
    const long =
      "C:\\Users\\bzp20\\Desktop\\ai项目开发\\frontend\\src\\components";
    const compact = compactPath(long, 30);

    expect(compact.length).toBeLessThanOrEqual(30);
    expect(compact).toContain("…");
    expect(compact.startsWith("C:\\Users\\")).toBe(true);
    expect(compact.endsWith("components")).toBe(true);
  });
});

describe("sessionRootLabel", () => {
  it("本地会话展示真实路径", () => {
    expect(sessionRootLabel("C:\\Users\\bzp20\\Desktop\\my-app", "local")).toBe(
      "C:\\Users\\bzp20\\Desktop\\my-app",
    );
  });

  it("demo 会话回退为仓库名", () => {
    expect(sessionRootLabel(null, "todo-app")).toBe("demo/todo-app");
    expect(sessionRootLabel(undefined, "express-demo")).toBe(
      "demo/express-demo",
    );
  });
});

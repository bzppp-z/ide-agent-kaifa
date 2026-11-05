import { describe, expect, it } from "vitest";

import { encodeFilePath, fileUrl } from "../api";

describe("encodeFilePath", () => {
  it("保留 / 分隔并逐段编码特殊字符", () => {
    expect(encodeFilePath("src/my file.ts")).toBe("src/my%20file.ts");
    expect(encodeFilePath("src/a#b/c?.ts")).toBe("src/a%23b/c%3F.ts");
    expect(encodeFilePath("文档/说明.md")).toBe(
      "%E6%96%87%E6%A1%A3/%E8%AF%B4%E6%98%8E.md",
    );
  });

  it("不会把 / 整体编码成 %2F", () => {
    expect(encodeFilePath("src/lib/api.ts")).toBe("src/lib/api.ts");
    expect(encodeFilePath("src/lib/api.ts")).not.toContain("%2F");
  });

  it("忽略空段，兼容开头/结尾斜杠", () => {
    expect(encodeFilePath("/src//index.ts/")).toBe("src/index.ts");
  });
});

describe("fileUrl", () => {
  const sessionId = "11111111-1111-1111-1111-111111111111";

  it("根目录必须带末尾斜杠", () => {
    expect(fileUrl(sessionId, "")).toBe(`/files/${sessionId}/`);
    expect(fileUrl(sessionId, "/")).toBe(`/files/${sessionId}/`);
  });

  it("子路径逐段编码", () => {
    expect(fileUrl(sessionId, "src/my file.ts")).toBe(
      `/files/${sessionId}/src/my%20file.ts`,
    );
  });
});

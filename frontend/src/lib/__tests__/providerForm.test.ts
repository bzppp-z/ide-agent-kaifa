import { describe, expect, it } from "vitest";

import {
  formatMaskedKey,
  providerCreatePayload,
  providerUpdatePayload,
  validateProviderForm,
} from "../providerForm";

describe("validateProviderForm", () => {
  it("名称 / Base URL / 模型名 为空时报错", () => {
    const errors = validateProviderForm({
      name: "",
      base_url: "",
      model: "",
      api_key: "",
    });

    expect(errors.name).toBeTruthy();
    expect(errors.base_url).toBeTruthy();
    expect(errors.model).toBeTruthy();
    expect(errors.api_key).toBeUndefined();
  });

  it("接受 http(s) 地址，API Key 允许为空", () => {
    expect(
      validateProviderForm({
        name: "DeepSeek",
        base_url: "https://api.deepseek.com/v1",
        model: "deepseek-chat",
        api_key: "",
      }),
    ).toEqual({});

    expect(
      validateProviderForm({
        name: "Ollama",
        base_url: "http://localhost:11434/v1",
        model: "llama3",
        api_key: "",
      }),
    ).toEqual({});
  });

  it("拒绝缺少协议或非 http(s) 的 Base URL", () => {
    const errors = validateProviderForm({
      name: "x",
      base_url: "api.deepseek.com/v1",
      model: "m",
      api_key: "",
    });
    expect(errors.base_url).toBeTruthy();

    expect(
      validateProviderForm({
        name: "x",
        base_url: "ftp://example.com/v1",
        model: "m",
        api_key: "",
      }).base_url,
    ).toBeTruthy();
  });
});

describe("provider payload", () => {
  it("编辑时 api_key 留空不放进 PATCH 载荷", () => {
    const payload = providerUpdatePayload({
      name: " DeepSeek ",
      base_url: "https://api.deepseek.com/v1/",
      model: " deepseek-chat ",
      api_key: "   ",
    });

    expect(payload).toEqual({
      name: "DeepSeek",
      base_url: "https://api.deepseek.com/v1",
      model: "deepseek-chat",
    });
    expect(payload).not.toHaveProperty("api_key");
  });

  it("编辑时填写新 key 会包含在载荷中", () => {
    const payload = providerUpdatePayload({
      name: "DeepSeek",
      base_url: "https://api.deepseek.com/v1",
      model: "deepseek-chat",
      api_key: "sk-new",
    });

    expect(payload.api_key).toBe("sk-new");
  });

  it("创建载荷去掉末尾斜杠与首尾空白", () => {
    expect(
      providerCreatePayload({
        name: " OpenAI ",
        base_url: "https://api.openai.com/v1///",
        model: " gpt-4o-mini ",
        api_key: "sk-123",
      }),
    ).toEqual({
      name: "OpenAI",
      base_url: "https://api.openai.com/v1",
      model: "gpt-4o-mini",
      api_key: "sk-123",
    });
  });
});

describe("formatMaskedKey", () => {
  it("空掩码展示为未设置", () => {
    expect(formatMaskedKey("")).toBe("（未设置）");
    expect(formatMaskedKey(null)).toBe("（未设置）");
    expect(formatMaskedKey(undefined)).toBe("（未设置）");
  });

  it("已有掩码原样展示", () => {
    expect(formatMaskedKey("sk-a...1234")).toBe("sk-a...1234");
  });
});

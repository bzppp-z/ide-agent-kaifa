import type { ProviderInput } from "./api";

export type ProviderFormValues = ProviderInput;

export type ProviderFormErrors = Partial<Record<keyof ProviderInput, string>>;

export interface ProviderPreset {
  id: string;
  label: string;
  base_url: string;
  model: string;
}

/** Base URL 快捷填充项（点击后写入 base_url / model，名称留空时补全）。 */
export const PROVIDER_PRESETS: ProviderPreset[] = [
  {
    id: "deepseek",
    label: "DeepSeek",
    base_url: "https://api.deepseek.com/v1",
    model: "deepseek-chat",
  },
  {
    id: "moonshot",
    label: "Moonshot",
    base_url: "https://api.moonshot.cn/v1",
    model: "moonshot-v1-8k",
  },
  {
    id: "openai",
    label: "OpenAI",
    base_url: "https://api.openai.com/v1",
    model: "gpt-4o-mini",
  },
  {
    id: "ollama",
    label: "Ollama",
    base_url: "http://localhost:11434/v1",
    model: "llama3",
  },
];

export function isHttpUrl(value: string): boolean {
  try {
    const url = new URL(value.trim());
    return (
      (url.protocol === "http:" || url.protocol === "https:") &&
      Boolean(url.host)
    );
  } catch {
    return false;
  }
}

/** 表单校验（与后端非空 / http(s) 规则一致；api_key 允许为空）。 */
export function validateProviderForm(
  values: ProviderFormValues,
): ProviderFormErrors {
  const errors: ProviderFormErrors = {};
  if (!values.name.trim()) errors.name = "请填写名称";
  if (!values.model.trim()) errors.model = "请填写模型名";

  const baseUrl = values.base_url.trim();
  if (!baseUrl) errors.base_url = "请填写 Base URL";
  else if (!isHttpUrl(baseUrl)) errors.base_url = "Base URL 需为 http(s) 地址";

  return errors;
}

/** PATCH 载荷：api_key 为空表示不修改，因此不放进请求体。 */
export function providerUpdatePayload(
  values: ProviderFormValues,
): Partial<ProviderInput> {
  const payload: Partial<ProviderInput> = {
    name: values.name.trim(),
    base_url: normalizeBaseUrl(values.base_url),
    model: values.model.trim(),
  };
  const apiKey = values.api_key.trim();
  if (apiKey) payload.api_key = apiKey;
  return payload;
}

export function providerCreatePayload(values: ProviderFormValues): ProviderInput {
  return {
    name: values.name.trim(),
    base_url: normalizeBaseUrl(values.base_url),
    model: values.model.trim(),
    api_key: values.api_key.trim(),
  };
}

/** 后端返回的掩码为空时展示「未设置」，避免出现空白列。 */
export function formatMaskedKey(masked: string | null | undefined): string {
  return masked?.trim() || "（未设置）";
}

function normalizeBaseUrl(value: string): string {
  return value.trim().replace(/\/+$/, "");
}

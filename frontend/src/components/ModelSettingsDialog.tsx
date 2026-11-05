import { useEffect, useState } from "react";

import { formatError, testProvider } from "../lib/api";
import {
  PROVIDER_PRESETS,
  formatMaskedKey,
  providerCreatePayload,
  providerUpdatePayload,
  validateProviderForm,
  type ProviderFormErrors,
  type ProviderFormValues,
} from "../lib/providerForm";
import type { Provider, ProviderTestResult } from "../lib/types";
import { cn } from "../lib/utils";
import { useProviderStore } from "../stores/useProviderStore";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Modal } from "./ui/modal";
import { Spinner } from "./ui/spinner";

const EMPTY_FORM: ProviderFormValues = {
  name: "",
  base_url: "",
  model: "",
  api_key: "",
};

function FieldError({ message }: { message?: string }) {
  return message ? (
    <p className="mt-0.5 text-[10.5px] text-red-400/90">{message}</p>
  ) : null;
}

function ProviderRow({
  provider,
  onActivate,
  onEdit,
}: {
  provider: Provider;
  onActivate: () => void;
  onEdit: () => void;
}) {
  const remove = useProviderStore((state) => state.remove);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<ProviderTestResult | null>(null);
  const [testError, setTestError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const runTest = async () => {
    setTesting(true);
    setTestResult(null);
    setTestError(null);
    try {
      setTestResult(await testProvider(provider.id));
    } catch (caught) {
      setTestError(formatError(caught));
    } finally {
      setTesting(false);
    }
  };

  const confirmDelete = async () => {
    setDeleting(true);
    setDeleteError(null);
    const ok = await remove(provider.id);
    setDeleting(false);
    if (!ok) {
      setDeleteError(useProviderStore.getState().error ?? "删除失败");
    }
  };

  return (
    <div
      className={cn(
        "rounded border px-3 py-2.5",
        provider.is_active
          ? "border-brand/30 bg-brand/5"
          : "border-surface-border bg-surface-alt",
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[12.5px] font-medium text-text">
          {provider.name}
        </span>
        {provider.is_active ? <Badge tone="brand">active</Badge> : null}
        <span className="truncate font-mono text-[11px] text-muted">
          {provider.model}
        </span>

        <div className="ml-auto flex flex-wrap items-center gap-1.5">
          {!provider.is_active ? (
            <Button size="sm" variant="ghost" onClick={onActivate}>
              启用
            </Button>
          ) : null}
          <Button size="sm" variant="ghost" onClick={onEdit}>
            编辑
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => void runTest()}
            disabled={testing}
          >
            {testing ? <Spinner size={10} /> : null}
            测试
          </Button>

          {confirming ? (
            <>
              <Button
                size="sm"
                variant="danger"
                onClick={() => void confirmDelete()}
                disabled={deleting}
              >
                {deleting ? <Spinner size={10} /> : null}
                确认删除
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setConfirming(false)}
                disabled={deleting}
              >
                取消
              </Button>
            </>
          ) : (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setConfirming(true)}
            >
              删除
            </Button>
          )}
        </div>
      </div>

      <div className="mt-1.5 grid gap-0.5 font-mono text-[10.5px] text-muted-dim">
        <span className="break-all" title={provider.base_url}>
          {provider.base_url}
        </span>
        <span>key: {formatMaskedKey(provider.api_key_masked)}</span>
      </div>

      {testResult ? (
        testResult.ok ? (
          <div className="mt-1.5 text-[11px] text-brand">
            延迟 {testResult.latency_ms}ms
          </div>
        ) : (
          <div className="mt-1.5 max-h-[52px] overflow-y-auto whitespace-pre-wrap break-words text-[11px] leading-relaxed text-red-400/90">
            {testResult.message || "连接失败"}
          </div>
        )
      ) : null}
      {testError ? (
        <div className="mt-1.5 max-h-[52px] overflow-y-auto whitespace-pre-wrap break-words text-[11px] leading-relaxed text-red-400/90">
          {testError}
        </div>
      ) : null}
      {deleteError ? (
        <div className="mt-1.5 whitespace-pre-wrap break-words text-[11px] text-red-400/90">
          {deleteError}
        </div>
      ) : null}
    </div>
  );
}

export function ModelSettingsDialog({ onClose }: { onClose: () => void }) {
  const providers = useProviderStore((state) => state.providers);
  const loading = useProviderStore((state) => state.loading);
  const error = useProviderStore((state) => state.error);
  const load = useProviderStore((state) => state.load);
  const create = useProviderStore((state) => state.create);
  const update = useProviderStore((state) => state.update);
  const activate = useProviderStore((state) => state.activate);

  const [form, setForm] = useState<ProviderFormValues>(EMPTY_FORM);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<ProviderFormErrors>({});
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    void load();
  }, [load]);

  const resetForm = () => {
    setForm(EMPTY_FORM);
    setEditingId(null);
    setFieldErrors({});
  };

  const setField = (key: keyof ProviderFormValues, value: string) => {
    setForm((previous) => ({ ...previous, [key]: value }));
    setFieldErrors((previous) => ({ ...previous, [key]: undefined }));
  };

  const applyPreset = (baseUrl: string, model: string, label: string) => {
    setForm((previous) => ({
      ...previous,
      base_url: baseUrl,
      model,
      name: previous.name.trim() ? previous.name : label,
    }));
    setFieldErrors({});
  };

  const startEdit = (provider: Provider) => {
    setEditingId(provider.id);
    setForm({
      name: provider.name,
      base_url: provider.base_url,
      model: provider.model,
      api_key: "",
    });
    setFieldErrors({});
  };

  const submit = async () => {
    const errors = validateProviderForm(form);
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) return;

    setSaving(true);
    const ok = editingId
      ? await update(editingId, providerUpdatePayload(form))
      : await create(providerCreatePayload(form));
    setSaving(false);
    if (ok) resetForm();
  };

  return (
    <Modal
      title="模型设置"
      subtitle="配置任意 OpenAI 兼容接口；未启用自定义模型时使用 Mock"
      onClose={onClose}
      widthClassName="max-w-2xl"
      footer={
        <Button variant="ghost" onClick={onClose}>
          关闭
        </Button>
      }
    >
      <div className="space-y-4">
        {error ? (
          <div className="whitespace-pre-wrap break-words rounded border border-red-500/25 bg-red-500/5 px-2.5 py-1.5 text-[11.5px] text-red-400/90">
            {error}
          </div>
        ) : null}

        <section>
          <div className="mb-2 flex items-center gap-2">
            <span className="text-[10.5px] uppercase tracking-wider text-muted-dim">
              已配置模型
            </span>
            {loading ? <Spinner size={10} /> : null}
            <Button
              size="sm"
              variant="ghost"
              className="ml-auto"
              disabled={loading}
              onClick={() => void load()}
            >
              刷新
            </Button>
          </div>

          {providers.length > 0 ? (
            <div className="space-y-2">
              {providers.map((provider) => (
                <ProviderRow
                  key={provider.id}
                  provider={provider}
                  onActivate={() => void activate(provider.id)}
                  onEdit={() => startEdit(provider)}
                />
              ))}
            </div>
          ) : loading ? (
            <div className="flex items-center gap-2 rounded border border-surface-border bg-surface-alt px-3 py-4 text-[11.5px] text-muted-dim">
              <Spinner size={11} />
              加载模型列表…
            </div>
          ) : (
            <div className="rounded border border-surface-border bg-surface-alt px-3 py-4 text-[11.5px] text-muted-dim">
              还没有自定义模型，当前使用 Mock。
            </div>
          )}
        </section>

        <section className="border-t border-surface-border pt-3">
          <div className="mb-2 flex items-center gap-2">
            <span className="text-[10.5px] uppercase tracking-wider text-muted-dim">
              {editingId ? "编辑模型" : "添加模型"}
            </span>
            {editingId ? (
              <Button size="sm" variant="ghost" onClick={resetForm}>
                取消编辑
              </Button>
            ) : null}
          </div>

          <div className="mb-2 flex flex-wrap gap-1.5">
            {PROVIDER_PRESETS.map((preset) => (
              <Button
                key={preset.id}
                size="sm"
                variant="outline"
                title={`${preset.base_url} · ${preset.model}`}
                onClick={() =>
                  applyPreset(preset.base_url, preset.model, preset.label)
                }
              >
                {preset.label}
              </Button>
            ))}
          </div>

          <div className="grid gap-2 sm:grid-cols-2">
            <div>
              <label className="mb-1 block text-[10.5px] uppercase tracking-wider text-muted-dim">
                名称
              </label>
              <Input
                value={form.name}
                onChange={(event) => setField("name", event.target.value)}
                placeholder="DeepSeek"
                disabled={saving}
              />
              <FieldError message={fieldErrors.name} />
            </div>

            <div>
              <label className="mb-1 block text-[10.5px] uppercase tracking-wider text-muted-dim">
                模型名
              </label>
              <Input
                value={form.model}
                onChange={(event) => setField("model", event.target.value)}
                placeholder="deepseek-chat"
                disabled={saving}
                className="font-mono"
              />
              <FieldError message={fieldErrors.model} />
            </div>

            <div className="sm:col-span-2">
              <label className="mb-1 block text-[10.5px] uppercase tracking-wider text-muted-dim">
                Base URL
              </label>
              <Input
                value={form.base_url}
                onChange={(event) => setField("base_url", event.target.value)}
                placeholder="https://api.deepseek.com/v1"
                disabled={saving}
                className="font-mono"
              />
              <FieldError message={fieldErrors.base_url} />
            </div>

            <div className="sm:col-span-2">
              <label className="mb-1 block text-[10.5px] uppercase tracking-wider text-muted-dim">
                API Key
              </label>
              <Input
                type="password"
                value={form.api_key}
                onChange={(event) => setField("api_key", event.target.value)}
                placeholder={editingId ? "留空则不修改" : "sk-..."}
                autoComplete="off"
                disabled={saving}
                className="font-mono"
              />
              <p className="mt-0.5 text-[10.5px] text-muted-dim">
                {editingId
                  ? "留空则不修改；填写新值会覆盖原 key"
                  : "留空表示无鉴权（例如本地 Ollama）"}
              </p>
            </div>
          </div>

          <div className="mt-2.5 flex items-center justify-end gap-2">
            <Button
              variant="brand"
              size="sm"
              onClick={() => void submit()}
              disabled={saving}
            >
              {saving ? <Spinner size={10} /> : null}
              {editingId ? "保存修改" : "添加模型"}
            </Button>
          </div>
        </section>
      </div>
    </Modal>
  );
}

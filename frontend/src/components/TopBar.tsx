import { useEffect, useState } from "react";

import { useProviderStore } from "../stores/useProviderStore";
import { useSessionStore } from "../stores/useSessionStore";
import { ModelSettingsDialog } from "./ModelSettingsDialog";
import { NewSessionDialog } from "./NewSessionDialog";
import { SandboxStatus } from "./SandboxStatus";
import { Button } from "./ui/button";
import { PlusIcon, SettingsIcon } from "./ui/icons";
import { Spinner } from "./ui/spinner";

export function TopBar({ onRetry }: { onRetry: () => void }) {
  const creating = useSessionStore((state) => state.creating);
  const providers = useProviderStore((state) => state.providers);
  const loadProviders = useProviderStore((state) => state.load);

  const [newSessionOpen, setNewSessionOpen] = useState(false);
  const [modelSettingsOpen, setModelSettingsOpen] = useState(false);

  useEffect(() => {
    void loadProviders();
  }, [loadProviders]);

  const activeProvider = providers.find((provider) => provider.is_active);
  const activeName = activeProvider?.name ?? "Mock";

  return (
    <header className="z-10 flex h-12 shrink-0 items-center gap-3 border-b border-surface-border bg-surface px-4">
      <div className="flex select-none items-center gap-2">
        <span className="flex h-6 w-6 items-center justify-center rounded border border-brand/30 bg-brand/10 font-mono text-[11px] font-bold text-brand">
          D
        </span>
        <span className="text-[15px] font-semibold tracking-tight text-text">
          <span className="text-brand">Dev</span>Flow
        </span>
      </div>

      <div className="ml-auto flex items-center gap-3">
        <Button
          size="sm"
          variant="ghost"
          className="max-w-[180px]"
          title={`模型设置 · 当前：${activeName}`}
          onClick={() => setModelSettingsOpen(true)}
        >
          <SettingsIcon className="h-3 w-3 shrink-0" />
          <span className="truncate">{activeName}</span>
        </Button>

        <SandboxStatus onRetry={onRetry} />

        <Button
          size="sm"
          variant="outline"
          onClick={() => setNewSessionOpen(true)}
          disabled={creating}
        >
          {creating ? <Spinner size={11} /> : <PlusIcon className="h-3 w-3" />}
          新会话
        </Button>
      </div>

      {newSessionOpen ? (
        <NewSessionDialog onClose={() => setNewSessionOpen(false)} />
      ) : null}

      {modelSettingsOpen ? (
        <ModelSettingsDialog onClose={() => setModelSettingsOpen(false)} />
      ) : null}
    </header>
  );
}

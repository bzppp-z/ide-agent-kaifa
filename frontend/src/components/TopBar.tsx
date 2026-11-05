import { DEMO_REPOS, useSessionStore } from "../stores/useSessionStore";
import { Button } from "./ui/button";
import { PlusIcon } from "./ui/icons";
import { SandboxStatus } from "./SandboxStatus";
import { Spinner } from "./ui/spinner";

export function TopBar({ onRetry }: { onRetry: () => void }) {
  const demoRepo = useSessionStore((state) => state.demoRepo);
  const creating = useSessionStore((state) => state.creating);
  const setDemoRepo = useSessionStore((state) => state.setDemoRepo);
  const createSession = useSessionStore((state) => state.createSession);

  const handleRepoChange = (repo: string) => {
    setDemoRepo(repo);
    void createSession(repo);
  };

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

      <div className="mx-1 h-5 w-px bg-surface-border" />

      <span className="text-[10.5px] uppercase tracking-wider text-muted-dim">
        repo
      </span>
      <select
        value={demoRepo}
        onChange={(event) => handleRepoChange(event.target.value)}
        disabled={creating}
        className="h-7 rounded border border-surface-border bg-surface-alt px-2 pr-6 text-[12px] text-muted outline-none hover:border-[#2E343A] focus:border-brand/40 disabled:opacity-50"
      >
        {DEMO_REPOS.map((repo) => (
          <option key={repo} value={repo}>
            {repo}
          </option>
        ))}
      </select>

      <div className="ml-auto flex items-center gap-3">
        <SandboxStatus onRetry={onRetry} />
        <Button
          size="sm"
          variant="outline"
          onClick={() => void createSession()}
          disabled={creating}
        >
          {creating ? <Spinner size={11} /> : <PlusIcon className="h-3 w-3" />}
          新会话
        </Button>
      </div>
    </header>
  );
}

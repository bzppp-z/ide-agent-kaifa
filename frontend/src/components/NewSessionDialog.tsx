import { useState } from "react";

import { isAbsolutePath } from "../lib/utils";
import { DEMO_REPOS, useSessionStore } from "../stores/useSessionStore";
import { DirectoryPicker } from "./DirectoryPicker";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Modal } from "./ui/modal";
import { Spinner } from "./ui/spinner";

type SessionMode = "demo" | "local";

function initialDemoRepo(repo: string): string {
  return (DEMO_REPOS as readonly string[]).includes(repo) ? repo : "todo-app";
}

export function NewSessionDialog({ onClose }: { onClose: () => void }) {
  const currentRepo = useSessionStore((state) => state.demoRepo);
  const creating = useSessionStore((state) => state.creating);
  const createSession = useSessionStore((state) => state.createSession);

  const [mode, setMode] = useState<SessionMode>("demo");
  const [demoRepo, setDemoRepo] = useState(() => initialDemoRepo(currentRepo));
  const [localPath, setLocalPath] = useState("");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setError(null);
    if (mode === "local") {
      const path = localPath.trim();
      if (!path) {
        setError("请输入本地目录路径");
        return;
      }
      if (!isAbsolutePath(path)) {
        setError("请输入绝对路径，例如 C:\\Users\\bzp20\\Desktop\\my-app");
        return;
      }
      const ok = await createSession({ localPath: path });
      if (ok) onClose();
      else setError(useSessionStore.getState().error ?? "创建会话失败");
      return;
    }

    const ok = await createSession({ demoRepo });
    if (ok) onClose();
    else setError(useSessionStore.getState().error ?? "创建会话失败");
  };

  return (
    <>
      <Modal
        title="新建会话"
        subtitle="选择内置 Demo，或直接打开本地目录"
        onClose={onClose}
        closeOnEsc={!pickerOpen}
        footer={
          <>
            <Button
              variant="ghost"
              onClick={onClose}
              disabled={creating}
            >
              取消
            </Button>
            <Button
              variant="brand"
              onClick={() => void submit()}
              disabled={creating}
            >
              {creating ? <Spinner size={11} /> : null}
              创建会话
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-2">
            <button
              type="button"
              onClick={() => setMode("demo")}
              className={
                mode === "demo"
                  ? "rounded border border-brand/40 bg-brand/10 px-3 py-2 text-left"
                  : "rounded border border-surface-border bg-surface-alt px-3 py-2 text-left hover:border-[#2E343A]"
              }
            >
              <div
                className={
                  mode === "demo"
                    ? "text-[12.5px] font-medium text-text"
                    : "text-[12.5px] font-medium text-muted"
                }
              >
                Demo 仓库
              </div>
              <div className="mt-0.5 text-[10.5px] text-muted-dim">
                内置示例项目
              </div>
            </button>

            <button
              type="button"
              onClick={() => setMode("local")}
              className={
                mode === "local"
                  ? "rounded border border-brand/40 bg-brand/10 px-3 py-2 text-left"
                  : "rounded border border-surface-border bg-surface-alt px-3 py-2 text-left hover:border-[#2E343A]"
              }
            >
              <div
                className={
                  mode === "local"
                    ? "text-[12.5px] font-medium text-text"
                    : "text-[12.5px] font-medium text-muted"
                }
              >
                本地目录
              </div>
              <div className="mt-0.5 text-[10.5px] text-muted-dim">
                真实目录上的文件与终端
              </div>
            </button>
          </div>

          {mode === "demo" ? (
            <div>
              <label className="mb-1 block text-[10.5px] uppercase tracking-wider text-muted-dim">
                Demo 仓库
              </label>
              <select
                value={demoRepo}
                onChange={(event) => setDemoRepo(event.target.value)}
                disabled={creating}
                className="h-8 w-full rounded border border-surface-border bg-surface-alt px-2.5 text-[12.5px] text-text outline-none hover:border-[#2E343A] focus:border-brand/40 disabled:opacity-50"
              >
                {DEMO_REPOS.map((repo) => (
                  <option key={repo} value={repo}>
                    {repo}
                  </option>
                ))}
              </select>
            </div>
          ) : (
            <div>
              <label className="mb-1 block text-[10.5px] uppercase tracking-wider text-muted-dim">
                本地目录（绝对路径）
              </label>
              <div className="flex items-center gap-2">
                <Input
                  value={localPath}
                  onChange={(event) => setLocalPath(event.target.value)}
                  spellCheck={false}
                  className="font-mono"
                  placeholder="C:\Users\bzp20\Desktop\my-app"
                  disabled={creating}
                />
                <Button
                  size="sm"
                  onClick={() => setPickerOpen(true)}
                  disabled={creating}
                >
                  浏览
                </Button>
              </div>
              <p className="mt-1 text-[10.5px] leading-relaxed text-muted-dim">
                文件树、编辑器和终端会直接作用于该目录，请确认路径无误。
              </p>
            </div>
          )}

          {error ? (
            <div className="whitespace-pre-wrap break-words rounded border border-red-500/25 bg-red-500/5 px-2.5 py-1.5 text-[11.5px] text-red-400/90">
              {error}
            </div>
          ) : null}
        </div>
      </Modal>

      {pickerOpen ? (
        <DirectoryPicker
          onClose={() => setPickerOpen(false)}
          onSelect={(path) => {
            setLocalPath(path);
            setMode("local");
            setPickerOpen(false);
          }}
        />
      ) : null}
    </>
  );
}

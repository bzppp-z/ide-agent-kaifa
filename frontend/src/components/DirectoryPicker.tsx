import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";

import { browseLocalDirs, formatError, getLocalHome } from "../lib/api";
import type { LocalBrowseResponse } from "../lib/types";
import { Button } from "./ui/button";
import { ArrowUpIcon, FolderIcon } from "./ui/icons";
import { Input } from "./ui/input";
import { Modal } from "./ui/modal";
import { Spinner } from "./ui/spinner";

interface DirectoryPickerProps {
  onSelect: (path: string) => void;
  onClose: () => void;
}

export function DirectoryPicker({ onSelect, onClose }: DirectoryPickerProps) {
  const [data, setData] = useState<LocalBrowseResponse | null>(null);
  const [manualPath, setManualPath] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const requestId = useRef(0);

  const navigate = async (path: string) => {
    const id = requestId.current + 1;
    requestId.current = id;
    setLoading(true);
    setError(null);
    try {
      const result = await browseLocalDirs(path);
      if (requestId.current !== id) return;
      setData(result);
      setManualPath(result.path);
    } catch (caught) {
      if (requestId.current !== id) return;
      setError(formatError(caught));
    } finally {
      if (requestId.current === id) setLoading(false);
    }
  };

  useEffect(() => {
    let alive = true;
    const start = async () => {
      try {
        const home = await getLocalHome();
        const result = await browseLocalDirs(home.path);
        if (!alive) return;
        setData(result);
        setManualPath(result.path);
      } catch (caught) {
        if (alive) setError(formatError(caught));
      } finally {
        if (alive) setLoading(false);
      }
    };
    void start();
    return () => {
      alive = false;
    };
  }, []);

  const handleJump = (event: FormEvent) => {
    event.preventDefault();
    const path = manualPath.trim();
    if (path) void navigate(path);
  };

  return (
    <Modal
      title="选择本地目录"
      subtitle="从主目录开始浏览，也可以直接输入绝对路径"
      onClose={onClose}
      widthClassName="max-w-xl"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button
            variant="brand"
            disabled={!data || loading}
            onClick={() => {
              if (data) onSelect(data.path);
            }}
          >
            选择此目录
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <div>
          <label className="mb-1 block text-[10.5px] uppercase tracking-wider text-muted-dim">
            路径
          </label>
          <form className="flex items-center gap-2" onSubmit={handleJump}>
            <Input
              value={manualPath}
              onChange={(event) => setManualPath(event.target.value)}
              spellCheck={false}
              className="font-mono"
              placeholder="C:\Users\..."
            />
            <Button type="submit" size="sm" disabled={loading}>
              跳转
            </Button>
          </form>
        </div>

        {data ? (
          <div className="rounded border border-surface-border bg-surface-alt px-2.5 py-2">
            <div
              className="break-all font-mono text-[11.5px] text-text"
              title={data.path}
            >
              {data.path}
            </div>
            <div className="mt-1 text-[10.5px] text-muted-dim">
              上级目录：{data.parent ?? "（已到根目录）"}
            </div>
          </div>
        ) : null}

        {error ? (
          <div className="whitespace-pre-wrap break-words rounded border border-red-500/25 bg-red-500/5 px-2.5 py-1.5 text-[11.5px] text-red-400/90">
            {error}
          </div>
        ) : null}

        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={loading || !data?.parent}
            onClick={() => {
              if (data?.parent) void navigate(data.parent);
            }}
          >
            <ArrowUpIcon className="h-3 w-3" />
            上一级
          </Button>
          {loading ? (
            <span className="flex items-center gap-1.5 text-[11px] text-muted-dim">
              <Spinner size={10} />
              加载目录…
            </span>
          ) : (
            <span className="text-[11px] text-muted-dim">
              {data ? `${data.entries.length} 个子目录` : ""}
            </span>
          )}
        </div>

        <div className="max-h-[280px] min-h-[120px] overflow-y-auto rounded border border-surface-border bg-bg">
          {data && data.entries.length > 0 ? (
            data.entries.map((entry) => (
              <button
                key={entry.path}
                type="button"
                title={entry.path}
                onClick={() => void navigate(entry.path)}
                className="flex w-full items-center gap-2 border-b border-surface-border/60 px-2.5 py-1.5 text-left text-[12.5px] text-muted last:border-b-0 hover:bg-surface-alt hover:text-text"
              >
                <FolderIcon className="h-3.5 w-3.5 shrink-0 text-muted-dim" />
                <span className="truncate">{entry.name}</span>
              </button>
            ))
          ) : !loading && !error ? (
            <div className="px-3 py-6 text-center text-[11.5px] text-muted-dim">
              该目录下没有子目录
            </div>
          ) : null}
        </div>
      </div>
    </Modal>
  );
}

import { useState } from "react";

import { cn } from "../lib/cn";
import { useEditorStore } from "../stores/useEditorStore";
import { Button } from "./ui/button";

function baseName(path: string): string {
  return path.split("/").pop() || path;
}

/** 多标签栏：文件名、未保存圆点、冲突黄点、agent 更新高亮与关闭确认。 */
export function EditorTabs() {
  const tabs = useEditorStore((state) => state.tabs);
  const activePath = useEditorStore((state) => state.activePath);
  const activate = useEditorStore((state) => state.activate);
  const closeTab = useEditorStore((state) => state.closeTab);
  const save = useEditorStore((state) => state.save);

  const [confirmPath, setConfirmPath] = useState<string | null>(null);

  if (tabs.length === 0) return null;

  const confirmTab = confirmPath
    ? tabs.find((tab) => tab.path === confirmPath)
    : undefined;

  const requestClose = (path: string, dirty: boolean) => {
    if (dirty) {
      setConfirmPath(path);
      return;
    }
    closeTab(path);
  };

  return (
    <>
      <div className="flex h-8 shrink-0 items-end gap-px overflow-x-auto border-b border-surface-border bg-surface">
        {tabs.map((tab) => {
          const active = tab.path === activePath;
          return (
            <div
              key={tab.path}
              role="tab"
              aria-selected={active}
              onClick={() => activate(tab.path)}
              onMouseDown={(event) => {
                // 中键关闭
                if (event.button === 1) {
                  event.preventDefault();
                  requestClose(tab.path, tab.dirty);
                }
              }}
              title={tab.path}
              className={cn(
                "group flex h-7 max-w-[200px] shrink-0 cursor-pointer items-center gap-2 border-r border-surface-border px-2.5 text-[12px]",
                active
                  ? "bg-surface-alt text-text shadow-[inset_0_1px_0_0_#0FDC78]"
                  : "text-muted hover:bg-surface-alt/60",
                tab.agentUpdated && !active && "bg-brand/5",
              )}
            >
              <span className="truncate font-mono">{baseName(tab.path)}</span>
              {tab.conflict ? (
                <span
                  className="h-1.5 w-1.5 shrink-0 rounded-full bg-yellow-400"
                  title="文件冲突"
                />
              ) : tab.dirty ? (
                <span
                  className="h-1.5 w-1.5 shrink-0 rounded-full bg-brand"
                  title="未保存"
                />
              ) : null}
              <button
                type="button"
                title="关闭"
                onClick={(event) => {
                  event.stopPropagation();
                  requestClose(tab.path, tab.dirty);
                }}
                className="flex h-4 w-4 shrink-0 items-center justify-center rounded text-muted-dim hover:bg-surface hover:text-text"
              >
                ×
              </button>
            </div>
          );
        })}
      </div>

      {confirmTab ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="w-full max-w-[320px] rounded-lg border border-surface-border bg-panel p-3.5 shadow-xl">
            <div className="text-[13px] text-text">关闭未保存的文件？</div>
            <div className="mt-1 break-all font-mono text-[11px] text-muted-dim">
              {confirmTab.path}
            </div>
            <div className="mt-3 flex justify-end gap-2">
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setConfirmPath(null)}
              >
                取消
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => {
                  setConfirmPath(null);
                  closeTab(confirmTab.path);
                }}
              >
                放弃
              </Button>
              <Button
                size="sm"
                variant="brand"
                onClick={() => {
                  setConfirmPath(null);
                  void save(confirmTab.path).then(() =>
                    closeTab(confirmTab.path),
                  );
                }}
              >
                保存
              </Button>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}

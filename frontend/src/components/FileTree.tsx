import { useState } from "react";

import { cn } from "../lib/utils";
import { useEditorStore } from "../stores/useEditorStore";
import { useSessionStore } from "../stores/useSessionStore";
import { useWorkspaceStore, type FileNode } from "../stores/useWorkspaceStore";
import { Button } from "./ui/button";
import { ChevronRight, FileIcon, FolderIcon, RefreshIcon } from "./ui/icons";
import { Spinner } from "./ui/spinner";

const EXT_COLORS: Record<string, string> = {
  ts: "#3B82F6",
  tsx: "#38BDF8",
  js: "#FACC15",
  jsx: "#FACC15",
  mjs: "#FACC15",
  cjs: "#FACC15",
  json: "#F59E0B",
  py: "#60A5FA",
  html: "#F97316",
  css: "#A78BFA",
  scss: "#A78BFA",
  md: "#34D399",
  yml: "#F472B6",
  yaml: "#F472B6",
  toml: "#F472B6",
  sh: "#4ADE80",
  ps1: "#4ADE80",
};

function fileColor(name: string): string {
  const extension = name.split(".").pop()?.toLowerCase() ?? "";
  return EXT_COLORS[extension] ?? "#8B949E";
}

function TreeNode({ node, depth }: { node: FileNode; depth: number }) {
  const [expanded, setExpanded] = useState(depth === 0 && node.type === "dir");

  const sessionId = useSessionStore((state) => state.id);
  const open = useEditorStore((state) => state.open);
  const selectedPath = useEditorStore((state) => state.filePath);

  const isDir = node.type === "dir";
  const selected = !isDir && selectedPath === node.path;

  const handleClick = () => {
    if (isDir) {
      setExpanded((value) => !value);
      return;
    }
    if (sessionId) void open(sessionId, node.path);
  };

  return (
    <div>
      <button
        type="button"
        onClick={handleClick}
        style={{ paddingLeft: 8 + depth * 12 }}
        className={cn(
          "group flex w-full items-center gap-1.5 py-[3px] pr-2 text-left text-[12.5px]",
          "hover:bg-surface-alt",
          selected
            ? "bg-surface-alt text-text shadow-[inset_2px_0_0_0_#0FDC78]"
            : "text-muted",
        )}
        title={node.path}
      >
        {isDir ? (
          <ChevronRight
            open={expanded}
            className="h-3 w-3 shrink-0 text-muted-dim"
          />
        ) : (
          <span className="w-3 shrink-0" />
        )}
        {isDir ? (
          <FolderIcon className="h-3.5 w-3.5 shrink-0 text-muted-dim" />
        ) : (
          <FileIcon
            className="h-3.5 w-3.5 shrink-0"
            style={{ color: fileColor(node.name) }}
          />
        )}
        <span className="truncate">{node.name}</span>
      </button>

      {isDir && expanded
        ? (node.children ?? []).map((child) => (
            <TreeNode key={child.path} node={child} depth={depth + 1} />
          ))
        : null}
    </div>
  );
}

export function FileTree() {
  const sessionId = useSessionStore((state) => state.id);
  const nodes = useWorkspaceStore((state) => state.nodes);
  const loading = useWorkspaceStore((state) => state.loading);
  const error = useWorkspaceStore((state) => state.error);
  const loadTree = useWorkspaceStore((state) => state.loadTree);

  return (
    <aside className="flex w-[260px] shrink-0 flex-col border-r border-surface-border bg-panel">
      <header className="flex h-9 shrink-0 items-center justify-between border-b border-surface-border px-3">
        <span className="text-[10.5px] uppercase tracking-wider text-muted-dim">
          文件
        </span>
        <Button
          variant="ghost"
          size="icon"
          title="刷新文件树"
          disabled={!sessionId || loading}
          onClick={() => {
            if (sessionId) void loadTree(sessionId);
          }}
          className="h-6 w-6"
        >
          {loading ? (
            <Spinner size={10} />
          ) : (
            <RefreshIcon className="h-3 w-3" />
          )}
        </Button>
      </header>

      <div className="flex-1 overflow-y-auto py-1">
        {!sessionId ? (
          <div className="flex items-center gap-2 px-3 py-4 text-[11.5px] text-muted-dim">
            <Spinner size={10} />
            正在创建会话…
          </div>
        ) : loading && nodes.length === 0 ? (
          <div className="flex items-center gap-2 px-3 py-4 text-[11.5px] text-muted-dim">
            <Spinner size={10} />
            正在启动沙箱…
          </div>
        ) : error ? (
          <div className="px-3 py-4 text-[11.5px] text-red-400/90">
            文件树加载失败：{error}
          </div>
        ) : nodes.length === 0 ? (
          <div className="px-3 py-4 text-[11.5px] text-muted-dim">
            空目录
          </div>
        ) : (
          nodes.map((node) => (
            <TreeNode key={node.path} node={node} depth={0} />
          ))
        )}
      </div>
    </aside>
  );
}

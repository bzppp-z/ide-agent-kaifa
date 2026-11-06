import { useEffect, useState } from "react";
import Editor, { type Monaco, type OnMount } from "@monaco-editor/react";

import { useEditorStore } from "../stores/useEditorStore";
import { Button } from "./ui/button";
import { Badge } from "./ui/badge";
import { Spinner } from "./ui/spinner";
import { EditorTabs } from "./EditorTabs";

function languageFor(path: string | null): string {
  if (!path) return "plaintext";
  const extension = path.split(".").pop()?.toLowerCase() ?? "";
  switch (extension) {
    case "ts":
    case "tsx":
      return "typescript";
    case "js":
    case "mjs":
    case "cjs":
    case "jsx":
      return "javascript";
    case "json":
      return "json";
    case "py":
      return "python";
    case "html":
      return "html";
    case "css":
      return "css";
    case "md":
      return "markdown";
    case "yml":
    case "yaml":
      return "yaml";
    default:
      return "plaintext";
  }
}

/** 自定义深色主题，主背景对齐品牌 #0B0D0E / 面板 #131619。 */
function defineDevFlowTheme(monaco: Monaco): void {
  monaco.editor.defineTheme("devflow-dark", {
    base: "vs-dark",
    inherit: true,
    rules: [],
    colors: {
      "editor.background": "#0B0D0E",
      "editor.foreground": "#E6E9EC",
      "editorLineNumber.foreground": "#4B5563",
      "editorLineNumber.activeForeground": "#8B949E",
      "editor.selectionBackground": "#0FDC7833",
      "editor.inactiveSelectionBackground": "#0FDC781A",
      "editor.lineHighlightBackground": "#131619",
      "editor.lineHighlightBorder": "#00000000",
      "editorCursor.foreground": "#0FDC78",
      "editorIndentGuide.background1": "#23282D",
      "editorIndentGuide.activeBackground1": "#3A4148",
      "editorWidget.background": "#131619",
      "editorWidget.border": "#23282D",
      "editorSuggestWidget.background": "#131619",
      "editorSuggestWidget.border": "#23282D",
      "editorSuggestWidget.selectedBackground": "#191D21",
      "editorGutter.background": "#0B0D0E",
      "editorOverviewRuler.border": "#23282D",
      "scrollbarSlider.background": "#23282D80",
      "scrollbarSlider.hoverBackground": "#2E343A",
    },
  });
}

function ConflictBanner({ path }: { path: string }) {
  const tab = useEditorStore((state) =>
    state.tabs.find((item) => item.path === path),
  );
  const resolveConflict = useEditorStore((state) => state.resolveConflict);
  const [showDiff, setShowDiff] = useState(false);

  useEffect(() => setShowDiff(false), [path]);

  if (!tab?.conflict) return null;
  const deleted = tab.conflict.agentContent === null;

  return (
    <div className="shrink-0 border-b border-yellow-500/25 bg-yellow-500/5 px-3 py-1.5">
      <div className="flex flex-wrap items-center gap-2 text-[11px] text-yellow-400/90">
        <span>
          {deleted
            ? "agent 删除了这个文件，但你有未保存的修改"
            : "agent 修改了这个文件，你的未保存内容未被覆盖"}
        </span>
        <div className="ml-auto flex items-center gap-1.5">
          <Button
            size="sm"
            variant="ghost"
            className="h-6 text-[11px]"
            onClick={() => setShowDiff((value) => !value)}
          >
            {showDiff ? "收起差异" : "查看差异"}
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="h-6 text-[11px]"
            onClick={() => resolveConflict(path, "mine")}
          >
            保留我的
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="h-6 text-[11px]"
            onClick={() => resolveConflict(path, "agent")}
          >
            {deleted ? "接受删除" : "使用 agent 版本"}
          </Button>
        </div>
      </div>

      {showDiff ? (
        <div className="mt-1.5 grid grid-cols-2 gap-2">
          <div className="min-w-0">
            <div className="mb-1 text-[10px] text-muted-dim">我的版本</div>
            <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded border border-surface-border bg-bg px-2 py-1.5 font-mono text-[10.5px] leading-relaxed text-muted">
              {tab.content || "（空）"}
            </pre>
          </div>
          <div className="min-w-0">
            <div className="mb-1 text-[10px] text-muted-dim">agent 版本</div>
            <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded border border-surface-border bg-bg px-2 py-1.5 font-mono text-[10.5px] leading-relaxed text-muted">
              {deleted ? "（文件已删除）" : tab.conflict.agentContent || "（空）"}
            </pre>
          </div>
        </div>
      ) : null}
    </div>
  );
}

export function MonacoEditor() {
  const tabs = useEditorStore((state) => state.tabs);
  const activePath = useEditorStore((state) => state.activePath);
  const handleChange = useEditorStore((state) => state.handleChange);
  const save = useEditorStore((state) => state.save);

  const activeTab = activePath
    ? tabs.find((tab) => tab.path === activePath)
    : undefined;

  const saveState = activeTab?.saving
    ? "保存中…"
    : activeTab?.dirty
      ? "未保存"
      : "已保存";

  const handleMount: OnMount = (editor, monaco) => {
    // Ctrl/Cmd+S 立即保存，阻止浏览器另存为。
    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () =>
      void useEditorStore.getState().save(),
    );
    editor.focus();
  };

  return (
    <section className="flex min-h-0 flex-1 flex-col bg-bg">
      <EditorTabs />

      <header className="flex h-9 shrink-0 items-center gap-2 border-b border-surface-border bg-surface px-3">
        <span className="truncate font-mono text-[12px] text-muted">
          {activeTab?.path ?? "未打开文件"}
        </span>
        {activeTab ? (
          <span className="ml-auto flex shrink-0 items-center gap-2">
            {activeTab.agentUpdated ? (
              <Badge tone="brand">agent 已更新</Badge>
            ) : null}
            {activeTab.truncated ? <Badge tone="warning">已截断</Badge> : null}
            {activeTab.loading ? (
              <Spinner size={10} />
            ) : (
              <Badge
                tone={
                  activeTab.saving
                    ? "warning"
                    : activeTab.dirty
                      ? "danger"
                      : "neutral"
                }
              >
                {saveState}
              </Badge>
            )}
            <Button
              size="sm"
              variant="outline"
              onClick={() => void save()}
              disabled={activeTab.saving || !activeTab.dirty}
            >
              保存
            </Button>
          </span>
        ) : null}
      </header>

      {activeTab?.conflict ? <ConflictBanner path={activeTab.path} /> : null}

      <div className="relative min-h-0 flex-1">
        {activeTab ? (
          <Editor
            height="100%"
            theme="devflow-dark"
            beforeMount={defineDevFlowTheme}
            onMount={handleMount}
            path={activeTab.path}
            language={languageFor(activeTab.path)}
            value={activeTab.content}
            onChange={(value) => handleChange(activeTab.path, value ?? "")}
            loading={
              <div className="flex h-full items-center gap-2 bg-bg px-4 text-[12px] text-muted-dim">
                <Spinner size={11} />
                加载编辑器…
              </div>
            }
            options={{
              fontFamily:
                '"JetBrains Mono", "Cascadia Code", Consolas, monospace',
              fontSize: 14,
              lineHeight: 1.65,
              minimap: { enabled: false },
              automaticLayout: true,
              smoothScrolling: false,
              scrollBeyondLastLine: false,
              padding: { top: 12, bottom: 12 },
              renderLineHighlight: "line",
              cursorBlinking: "smooth",
              wordWrap: "off",
              tabSize: 2,
            }}
          />
        ) : (
          <div className="flex h-full items-center justify-center bg-bg px-6 text-center text-[12px] text-muted-dim">
            从左侧文件树选择一个文件开始编辑
          </div>
        )}

        {activeTab?.error ? (
          <div className="absolute inset-x-0 bottom-0 border-t border-red-500/20 bg-red-500/5 px-3 py-1.5 text-[11px] text-red-400/90">
            {activeTab.error}
          </div>
        ) : null}
      </div>
    </section>
  );
}

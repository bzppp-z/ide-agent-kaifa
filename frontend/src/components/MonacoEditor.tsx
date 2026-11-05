import Editor, { type Monaco, type OnMount } from "@monaco-editor/react";

import { useEditorStore } from "../stores/useEditorStore";
import { Button } from "./ui/button";
import { Badge } from "./ui/badge";
import { Spinner } from "./ui/spinner";

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

function baseName(path: string): string {
  return path.split("/").pop() || path;
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

export function MonacoEditor() {
  const filePath = useEditorStore((state) => state.filePath);
  const content = useEditorStore((state) => state.content);
  const dirty = useEditorStore((state) => state.dirty);
  const saving = useEditorStore((state) => state.saving);
  const loading = useEditorStore((state) => state.loading);
  const truncated = useEditorStore((state) => state.truncated);
  const error = useEditorStore((state) => state.error);
  const handleChange = useEditorStore((state) => state.handleChange);
  const save = useEditorStore((state) => state.save);
  const close = useEditorStore((state) => state.close);

  const saveState = saving ? "保存中…" : dirty ? "未保存" : "已保存";

  const handleMount: OnMount = (editor, monaco) => {
    // Ctrl/Cmd+S 立即保存，阻止浏览器另存为。
    editor.addCommand(
      monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS,
      () => void useEditorStore.getState().save(),
    );
    editor.focus();
  };

  return (
    <section className="flex min-h-0 flex-1 flex-col bg-bg">
      {filePath ? (
        <div className="flex h-8 shrink-0 items-end gap-px overflow-x-auto border-b border-surface-border bg-surface">
          <div className="flex h-7 items-center gap-2 border-r border-surface-border bg-surface-alt px-2.5 text-[12px] text-text">
            <span className="truncate font-mono">{baseName(filePath)}</span>
            {dirty ? (
              <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-brand" />
            ) : null}
            <button
              type="button"
              title="关闭"
              onClick={close}
              className="ml-1 flex h-4 w-4 items-center justify-center rounded text-muted-dim hover:bg-surface hover:text-text"
            >
              ×
            </button>
          </div>
        </div>
      ) : null}

      <header className="flex h-9 shrink-0 items-center gap-2 border-b border-surface-border bg-surface px-3">
        <span className="truncate font-mono text-[12px] text-muted">
          {filePath ?? "未打开文件"}
        </span>
        {filePath ? (
          <span className="ml-auto flex shrink-0 items-center gap-2">
            {truncated ? <Badge tone="warning">已截断</Badge> : null}
            {loading ? (
              <Spinner size={10} />
            ) : (
              <Badge tone={saving ? "warning" : dirty ? "danger" : "neutral"}>
                {saveState}
              </Badge>
            )}
            <Button
              size="sm"
              variant="outline"
              onClick={() => void save()}
              disabled={saving || !dirty}
            >
              保存
            </Button>
          </span>
        ) : null}
      </header>

      <div className="relative min-h-0 flex-1">
        {filePath ? (
          <Editor
            height="100%"
            theme="devflow-dark"
            beforeMount={defineDevFlowTheme}
            onMount={handleMount}
            path={filePath}
            language={languageFor(filePath)}
            value={content}
            onChange={(value) => handleChange(value ?? "")}
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

        {error && filePath ? (
          <div className="absolute inset-x-0 bottom-0 border-t border-red-500/20 bg-red-500/5 px-3 py-1.5 text-[11px] text-red-400/90">
            {error}
          </div>
        ) : null}
      </div>
    </section>
  );
}

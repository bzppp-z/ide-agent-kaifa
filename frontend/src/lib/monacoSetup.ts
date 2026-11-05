import * as monaco from "monaco-editor";
import { loader } from "@monaco-editor/react";
import editorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";
import jsonWorker from "monaco-editor/esm/vs/language/json/json.worker?worker";
import cssWorker from "monaco-editor/esm/vs/language/css/css.worker?worker";
import htmlWorker from "monaco-editor/esm/vs/language/html/html.worker?worker";
import tsWorker from "monaco-editor/esm/vs/language/typescript/ts.worker?worker";

interface MonacoEnvironmentLike {
  getWorker: (workerId: string, label: string) => Worker;
}

/**
 * 使用本地 monaco-editor（不走 CDN），并注册各语言的 Web Worker。
 */
export function configureMonaco(): void {
  (self as unknown as { MonacoEnvironment: MonacoEnvironmentLike }).MonacoEnvironment =
    {
      getWorker(_workerId: string, label: string): Worker {
        switch (label) {
          case "json":
            return new jsonWorker();
          case "css":
          case "scss":
          case "less":
            return new cssWorker();
          case "html":
          case "handlebars":
          case "razor":
            return new htmlWorker();
          case "typescript":
          case "javascript":
            return new tsWorker();
          default:
            return new editorWorker();
        }
      },
    };

  loader.config({ monaco });
}

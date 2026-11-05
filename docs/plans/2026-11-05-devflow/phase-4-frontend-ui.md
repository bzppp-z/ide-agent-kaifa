# Phase 4: Frontend UI + Three-Column Layout

**Goal:** React SPA 完整界面。三栏布局左 260px + 中 Monaco + 右 380px + 底部 200px xterm，深色 IDE 主背景 #171717，品牌绿 #0FDC78 作为 agent 面板/工具卡片/接受按钮识别锚点。SSE 和 WebSocket hook 跑通——后端 Phase 3 已经有完整 API，前端接到就能看到实时流。

**Prerequisites:** Phase 1–3 全部完成。后端 API 跑在 http://localhost:8000，所有 API 路径和 SSE/WS 格式已稳定。
**Deliverables:** 浏览器打开 http://localhost:5173 看到完整界面、能创建 session、能看到 SSE 事件流、能连 WS 终端。

**Interface Decisions Implemented:** 对应 spec Interface Design Contract 的所有视觉 + 布局决策。

> **本地模式调整：**
> - **shadcn/ui**：先尝试 `npx shadcn@latest init -d` + `add`；若因交互/网络/registry 失败，**直接手写等价 Tailwind 组件**（`components/ui/button.tsx`、`input.tsx`、`badge.tsx`、`card.tsx`），保持 API 与 shadcn 相同即可，不要卡住。
> - **终端**：本地 shell 的 `pwd` 输出是本地 workspace 绝对路径（不是 `/workspace`）。验收改为"输出包含会话 workspace 目录且命令可执行"。
> - **文件树**：必须过滤以 `.` 开头的内部文件（如 `.devflow-initialized`）。
> - **API 根路径**：文件树根目录请求是 `GET /files/{session_id}/`（末尾斜杠）；拼接子路径时用逐段 `encodeURIComponent`，不要把 `/` 整体编码。
> - Token 只存内存（Zustand），刷新丢失由 Phase 5 处理。

---

## Task 4.1: Vite + React + Tailwind + shadcn/ui 初始化 + 主题 tokens

**Goal:** 前端项目脚手架搭起来。Tailwind + shadcn/ui + 品牌色 tokens + 深色主题 + 全局样式。

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/tsconfig.json`
- Create: `frontend/tailwind.config.ts`
- Create: `frontend/postcss.config.js`
- Create: `frontend/index.html`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/index.css`
- Create: `frontend/src/theme.ts`
- Create: `frontend/.env.local`（VITE_API_BASE_URL=http://localhost:8000）

**Steps:**
- [x] 初始化（**注意**：不要用 `npm create vite@latest .` 的交互式流程，直接手写 package.json 更稳）：
  ```
  npm install
  npm install monaco-editor @monaco-editor/react zustand
  npm install -D tailwindcss@3 postcss autoprefixer typescript vite @vitejs/plugin-react @types/react @types/react-dom
  ```
  说明：Tailwind 用 v3（v4 的 PostCSS 插件配置不同，容易踩坑）；xterm 用 `@xterm/xterm`（新版包名）。
  若 shadcn CLI 可用则用，否则按上面的手写组件方案。
- [x] 写 `tailwind.config.ts`，注入品牌色 tokens：
  ```ts
  export default {
    content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
    theme: {
      extend: {
        colors: {
          brand: { DEFAULT: "#0FDC78", hover: "#14EF85", dim: "#0AAE5E" },
          surface: { DEFAULT: "#171717", alt: "#1E1E1E", border: "#262626" },
          muted: { DEFAULT: "#A1A1AA", dim: "#737373" },
        },
        fontFamily: { mono: ["JetBrains Mono", "monospace"], sans: ["Inter", "PingFang SC", "sans-serif"] },
        borderRadius: { DEFAULT: "8px", lg: "12px" },
      },
    },
    plugins: [],
  };
  ```
- [x] 写 `index.css`：Tailwind directives + CSS variables + 深色 body + xterm 导入 + Monaco 样式覆盖：
  ```css
  @tailwind base;
  @tailwind components;
  @tailwind utilities;

  body { @apply bg-surface text-muted font-sans; }
  :root { --brand: #0FDC78; }

  /* Monaco Dark+ 覆盖（默认 Monaco Dark+ 够用，MVP 不改） */
  /* xterm 样式 */
  @import "xterm/css/xterm.css";
  ```
- [x] 验证 `npm run dev -- --port 5173` 启动无报错，浏览器看到空白深色页面。

---

## Task 4.2: Zustand stores + API client + SSE hook + WS hook

**Goal:** 5 个 Zustand store + fetch-based API client + `useSSE` hook + `useTerminalWS` hook。所有后端交互走这层，组件不直接 fetch。

**Files:**
- Create: `frontend/src/lib/api.ts`
- Create: `frontend/src/lib/sse.ts`
- Create: `frontend/src/stores/useSessionStore.ts`
- Create: `frontend/src/stores/useWorkspaceStore.ts`
- Create: `frontend/src/stores/useEditorStore.ts`
- Create: `frontend/src/stores/useChatStore.ts`
- Create: `frontend/src/stores/useTerminalStore.ts`

**Steps:**
- [x] 写 `lib/api.ts`：统一 fetch wrapper + 带 Authorization header：
  ```ts
  const API_BASE = import.meta.env.VITE_API_BASE_URL;
  export async function apiFetch<T>(path: string, opts?: RequestInit): Promise<T> {
    const token = useSessionStore.getState().token;
    const res = await fetch(`${API_BASE}${path}`, {
      ...opts,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...opts?.headers,
      },
    });
    if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`);
    return res.json();
  }
  ```
- [x] 写 `lib/sse.ts` + `useSSE` hook：
  ```ts
  export function useSSE(sessionId: string | null) {
    useEffect(() => {
      if (!sessionId) return;
      let cancelled = false;
      const connect = async () => {
        // 每 2 秒重连 + 发送 Last-Event-ID
        while (!cancelled) {
          try {
            const token = useSessionStore.getState().token;
            const res = await fetch(`${API_BASE}/agent/stream/${sessionId}`, {
              headers: { Authorization: `Bearer ${token}`, "Last-Event-ID": lastEventId },
            });
            const reader = res.body!.getReader();
            // 逐 chunk 解析 SSE: event: xxx\ndata: {...}\n\n
            // 解析后调用 useChatStore.getState().pushEvent(parsedEvent)
            // 检测 done 事件 → break
          } catch (e) {
            await new Promise((r) => setTimeout(r, 2000));
          }
        }
      };
      connect();
      return () => { cancelled = true; };
    }, [sessionId]);
  }
  ```
- [x] 写 `useTerminalWS` hook：WebSocket 连接 `/terminal/ws/{session_id}?token=xxx`，每 1 秒重连 + 指数退避。onmessage → `useTerminalStore.getState().append(data)`；send → websocket.send。
- [x] 5 个 store 结构：
  - `useSessionStore`: { id, token, demoRepo, containerStatus: "green"|"yellow"|"red" } + createSession() + setContainerStatus()
  - `useWorkspaceStore`: { files: FileNode[] } + loadFiles() + selectFile()
  - `useEditorStore`: { openFile: string | null, content: string, dirty: boolean } + save()
  - `useChatStore`: { events: ChatEvent[] } + sendMessage() + pushEvent() + clear()
  - `useTerminalStore`: { lines: string[] } + append() + clear() + send()

**Security/Boundary Notes:** JWT token **只存 Zustand store（内存）**，不存 localStorage/sessionStorage——刷新页面就丢 token，需要重新创建 session（MVP 简化；完整恢复逻辑在 Phase 5 补）。

---

## Task 4.3: App.tsx 三栏布局 + TopBar + SandboxStatus

**Goal:** 三栏布局外壳 + 顶栏组件 + 沙箱状态指示器。顶栏含 DevFlow logo + Demo repo 下拉 + 新会话按钮。三栏：左 260px（文件树）+ 中 flex（Monaco）+ 右 380px（对话面板），底部 200px 终端。容器未就绪时文件树显示 loading、SandboxStatus 显示 yellow。

**Files:**
- Create: `frontend/src/App.tsx`
- Create: `frontend/src/components/TopBar.tsx`
- Create: `frontend/src/components/SandboxStatus.tsx`
- Create/Modify: `frontend/src/index.css`（布局样式）

**Steps:**
- [x] 写 `App.tsx`（Grid / Flex 布局）：
  ```tsx
  return (
    <div className="h-screen w-screen flex flex-col bg-surface text-muted">
      <TopBar />
      <div className="flex flex-1 overflow-hidden">
        <FileTree className="w-[260px] flex-shrink-0 border-r border-surface-border overflow-y-auto" />
        <div className="flex-1 flex flex-col overflow-hidden min-w-0">
          <MonacoEditor className="flex-1 overflow-hidden" />
          <Terminal className="h-[200px] flex-shrink-0 border-t border-surface-border" />
        </div>
        <ChatPanel className="w-[380px] flex-shrink-0 border-l border-surface-border overflow-y-auto" />
      </div>
    </div>
  );
  ```
- [x] 写 `TopBar.tsx`：左侧 DevFlow logo（"Dev" 用品牌绿）+ 中间 Demo repo 下拉（todo-app / express-demo / python-flask-demo）+ 右侧 SandboxStatus（green/yellow/red 小圆点 + 文字 + 重试按钮）+ 新会话按钮。
- [x] 写 `SandboxStatus.tsx`：
  ```tsx
  <div className="flex items-center gap-2 text-xs">
    <span className={`w-2 h-2 rounded-full ${
      containerStatus === "green" ? "bg-brand" :
      containerStatus === "yellow" ? "bg-yellow-400 animate-pulse" :
      "bg-red-500"
    }`} />
    <span>沙箱 {containerStatus === "green" ? "就绪" : containerStatus === "yellow" ? "启动中..." : "失败"}</span>
    {containerStatus === "red" && <button onClick={retry}>重试</button>}
  </div>
  ```
- [x] **视觉验收**：打开页面能看到完整三栏布局、深色背景、品牌绿 logo 识别锚点。

**Visual Verification:**
- [x] 宽屏（1920×1080）三栏并列，文件树左 260px、Monaco 中间弹性、对话面板右 380px
- [x] 底部终端高 200px 可见
- [x] 整体深色背景 #171717 覆盖全屏
- [x] 顶部 DevFlow logo "Dev" 用 #0FDC78 品牌绿
- [x] 无渐变、无装饰背景图案

---

## Task 4.4: FileTree + MonacoEditor（核心编辑区）

**Goal:** 文件树组件 + Monaco 编辑器组件。文件树从 workspace store 加载；点击文件树 → Monaco 显示内容；Monaco 编辑 300ms debounce → POST save。

**Files:**
- Create: `frontend/src/components/FileTree.tsx`
- Create: `frontend/src/components/MonacoEditor.tsx`

**Steps:**
- [x] 写 `FileTree.tsx`：递归渲染目录树；loading 状态（容器未就绪时显示"正在启动沙箱..."spinner）；hover/选中状态（shadcn Button + cursor pointer）；点击 → `useEditorStore.getState().selectFile(filePath)` 并触发 `apiFetch("GET /files/{session_id}/{path}")` → Monaco 显示内容。
- [x] 写 `MonacoEditor.tsx`：
  ```tsx
  const MonacoEditor = ({ content, onChange }) => {
    return (
      <Editor
        height="100%"
        theme="vs-dark"        // Monaco Dark+ 主题
        defaultLanguage="typescript"
        value={content}
        onChange={onChange}
        options={{
          fontFamily: "JetBrains Mono",
          fontSize: 14,
          minimap: { enabled: false },
          automaticLayout: true,
          smoothScrolling: true,
        }}
      />
    );
  };
  ```
- [x] autosave debounce 300ms：
  ```ts
  const debouncedSave = useMemo(() => debounce(async (content) => {
    await apiFetch(`/files/${sessionId}/${encodeURIComponent(filePath)}`, {
      method: "POST", body: JSON.stringify({ content }),
    });
  }, 300), [sessionId, filePath]);
  ```

**Visual Verification:**
- [x] 点击文件树某文件 → Monaco 内容更新
- [x] Monaco 编辑后停止 300ms → 自动保存（DevTools 能看到 POST /files 请求）
- [x] Monaco 主题是深色（Dark+）
- [x] 文件树选中项有高亮

---

## Task 4.5: ChatPanel + SSE 事件渲染 + ToolCard（签名元素）

**Goal:** 对话面板组件。左侧用户消息气泡、右侧 agent 流式文本渲染、工具调用卡片渲染（签名元素，可展开入参/执行时长/结果摘要）、流式消息光标闪烁。

**Files:**
- Create: `frontend/src/components/ChatPanel.tsx`
- Create: `frontend/src/components/ToolCard.tsx`
- Create: `frontend/src/components/SseMessage.tsx`

**Steps:**
- [x] 写 `ChatPanel.tsx`：用户输入框（textarea + 发送按钮）+ 消息列表（按时间排列）。
- [x] 写消息渲染逻辑：按 `event.type` 渲染不同组件：
  ```tsx
  {events.map((ev, i) => {
    switch (ev.type) {
      case "message.delta": return <SseMessage key={i} content={ev.data.content} streaming={isStreaming} />;
      case "tool.call":     return <ToolCard key={i} state="calling" tool={ev.data} />;
      case "tool.result":   return <ToolCard key={i} state="result" tool={ev.data} />;
      case "error":         return <ErrorMessage key={i} error={ev.data} />;
      case "done":          return null;
    }
  })}
  ```
- [x] 写 `ToolCard.tsx`（**签名元素，视觉锚点**）：
  ```tsx
  // 品牌绿 #0FDC78 用于：
  // - 卡片左侧 border-left 2px solid brand
  // - 标题栏 "✓" 成功徽章（ok=true） / "✗" 红色失败徽章（ok=false）
  // - 执行时长文字 muted dim
  // - 默认折叠入参 → 点击展开显示 JSON（mono font）
  // - 结果摘要截断 200 字符 → 末尾 "..." 可点击展开完整
  ```
- [x] 流式光标闪烁：streaming 状态下 SseMessage 末尾有一个 `|` 字符，用 CSS animation blink 每秒闪一次。

**Visual Verification:**
- [x] 用户消息和 agent 消息分左右（或分色）
- [x] ToolCard 左侧品牌绿 border 识别锚点明显
- [x] 流式 agent 文本逐字流出 + 光标闪烁
- [x] ToolCard 入参默认折叠，点击展开显示 JSON
- [x] 所有事件类型都有对应渲染（tool.call / tool.result / message.delta / error）

---

## Task 4.6: Terminal（xterm.js + WebSocket 桥）+ Sandbox 状态联动

**Goal:** 底部终端组件，xterm.js + WebSocket 桥。容器未就绪时显示"等待沙箱启动..."；WebSocket 连接状态有 indicator（连接中 spinner / 就绪 / 断开重连）。

**Files:**
- Create: `frontend/src/components/Terminal.tsx`

**Steps:**
- [x] 写 `Terminal.tsx`：
  ```tsx
  const termRef = useRef<HTMLDivElement>(null);
  const term = useMemo(() => new Terminal({ theme: { background: "#0A0A0A", foreground: "#A1A1AA" }, fontSize: 13 }), []);

  useEffect(() => {
    if (!termRef.current) return;
    term.open(termRef.current);
    // 连 WebSocket：useTerminalWS
    ws.onmessage = (ev) => term.write(ev.data);
    // 用户输入 → ws.send(data)
    term.onData((data) => ws.send(data));
  }, []);
  ```
- [x] WebSocket 状态与 SandboxStatus 联动：WS 就绪 → 顶栏 SandboxStatus 变 green；WS 失败（容器未就绪）→ SandboxStatus 变 yellow；WS 连续重试失败 → red。
- [x] 终端高度固定 200px 可拖拽（MVP 只固定高度，拖拽在 Roadmap）。

**Visual Verification:**
- [x] 容器未就绪时终端区域显示"等待沙箱启动..."
- [x] WS 连接成功后 xterm 显示 shell 提示符
- [x] 输入命令 `pwd` 能输出包含会话 workspace 目录的本地路径
- [x] WS 断开时显示重连 spinner

---

## Phase Completion Checklist

- [x] 浏览器打开 http://localhost:5173 看到完整三栏布局 + 深色主题 + 品牌绿识别锚点
- [x] 顶栏有 DevFlow logo（"Dev" 绿色）+ Demo repo 下拉 + SandboxStatus 指示点
- [x] 左侧文件树 260px，Monaco 中间弹性 380px 右侧对话面板
- [x] Monaco Dark+ 主题、JetBrains Mono 字体、autosave debounce 300ms 工作
- [x] ChatPanel 能渲染 tool.call / tool.result / message.delta / error 事件
- [x] ToolCard 签名元素（品牌绿 border + ✓/✗ 徽章 + 可展开入参 + 时长）视觉正确
- [x] 流式 agent 文本逐字流出 + 光标闪烁
- [x] 底部 200px xterm 终端，输入 `pwd` 能输出会话 workspace 本地路径
- [x] Zustand 5 个 store 全部接通后端 API
- [x] `useSSE` 和 `useTerminalWS` hook 断线自动重连
- [x] npm build 无报错、无 TypeScript 类型错误
- [x] 所有 `@ts-ignore` 或 `any` 类型仅限 mock 或无法规避的第三方类型缺失

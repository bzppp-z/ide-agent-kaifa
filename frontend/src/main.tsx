import ReactDOM from "react-dom/client";

import "@xterm/xterm/css/xterm.css";
import "./index.css";

import App from "./App";
import { configureMonaco } from "./lib/monacoSetup";

configureMonaco();

// 不使用 StrictMode：SSE/WS effect 双挂载会短暂断开后端本次 run，MVP 阶段避免该干扰。
ReactDOM.createRoot(document.getElementById("root")!).render(<App />);

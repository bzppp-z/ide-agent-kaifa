# DevFlow Phase 3 手工验证命令清单

后端启动（mock provider，不需要 DEEPSEEK_API_KEY）：

```powershell
# 在 backend 目录下
C:\Users\bzp20\miniconda3\envs\agent-dev\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

> 说明：HTTP/1.1 的 SSE 是长连接，PowerShell 的 `Invoke-WebRequest` 会等连接结束后
> 才返回全部内容；要看逐事件输出请用 `curl.exe -N` 或 `python scripts/e2e_api_demo.py`。

## 0. 健康检查

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
# {"ok":true,"db":true,"sandbox":true}
```

```bash
curl.exe http://127.0.0.1:8000/health
```

## 1. 创建会话

```powershell
$created = Invoke-RestMethod -Method Post http://127.0.0.1:8000/sessions `
  -ContentType 'application/json' -Body '{"demo_repo":"todo-app"}'
$id = $created.id
$token = $created.token
$created | ConvertTo-Json
```

```bash
curl.exe -X POST http://127.0.0.1:8000/sessions \
  -H "Content-Type: application/json" -d "{\"demo_repo\":\"todo-app\"}"
```

响应包含 `id` / `token` / `demo_repo` / `container_ready`（首次为 false）/ `created_at`。

## 2. 触发 agent run

```powershell
Invoke-RestMethod -Method Post "http://127.0.0.1:8000/sessions/$id/run" `
  -Headers @{Authorization="Bearer $token"} `
  -ContentType 'application/json' -Body '{"message":"帮我看看 src 目录"}'
# {"run_id":"..."}
```

```bash
curl.exe -X POST "http://127.0.0.1:8000/sessions/$id/run" \
  -H "Authorization: Bearer $token" \
  -H "Content-Type: application/json" -d "{\"message\":\"帮我看看 src 目录\"}"
```

## 3. 读取 SSE（-N 关闭缓冲）

```powershell
curl.exe -N "http://127.0.0.1:8000/agent/stream/$id" -H "Authorization: Bearer $token"
# event: tool.call
# data: {"call_id":"mock-call-1","tool_name":"list","arguments":{"path":"src"}}
# event: tool.result
# data: {"call_id":"mock-call-1","tool_name":"list","ok":true,...}
# event: message.delta
# data: {"content":"好的，我已"}
# ...
# event: done
# data: {"run_id":"...","total_tokens":50,"total_steps":1}
```

也可以用 query token（浏览器 EventSource 无法自定义 header）：

```bash
curl.exe -N "http://127.0.0.1:8000/agent/stream/$id?token=$token"
```

## 4. 文件树 / 读文件 / 写文件

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/files/$id/" -Headers @{Authorization="Bearer $token"}
Invoke-RestMethod "http://127.0.0.1:8000/files/$id/src" -Headers @{Authorization="Bearer $token"}
Invoke-RestMethod "http://127.0.0.1:8000/files/$id/src/index.js" -Headers @{Authorization="Bearer $token"}

Invoke-RestMethod -Method Post "http://127.0.0.1:8000/files/$id/demo/hello.txt" `
  -Headers @{Authorization="Bearer $token"} -ContentType 'application/json' `
  -Body '{"content":"hello devflow"}'
# {"ok":true,"path":"demo/hello.txt"}
```

```bash
curl.exe "http://127.0.0.1:8000/files/$id/" -H "Authorization: Bearer $token"
curl.exe "http://127.0.0.1:8000/files/$id/src/index.js" -H "Authorization: Bearer $token"
curl.exe -X POST "http://127.0.0.1:8000/files/$id/demo/hello.txt" \
  -H "Authorization: Bearer $token" -H "Content-Type: application/json" \
  -d "{\"content\":\"hello devflow\"}"
```

越界路径（`../`、`C:\...`）返回 403，不存在的文件返回 404。

## 5. 恢复会话历史（F5 刷新 / 重启后端后）

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/sessions/$id" -Headers @{Authorization="Bearer $token"} |
  ConvertTo-Json -Depth 5
```

```bash
curl.exe "http://127.0.0.1:8000/sessions/$id" -H "Authorization: Bearer $token"
```

## 6. 删除会话（清理 workspace，保留消息）

```powershell
Invoke-RestMethod -Method Delete "http://127.0.0.1:8000/sessions/$id" `
  -Headers @{Authorization="Bearer $token"}
```

```bash
curl.exe -X DELETE "http://127.0.0.1:8000/sessions/$id" -H "Authorization: Bearer $token"
```

## 7. WebSocket 终端

浏览器/前端用 `ws://127.0.0.1:8000/terminal/ws/{id}?token={token}`；
命令行验证脚本（Python）：

```powershell
C:\Users\bzp20\miniconda3\envs\agent-dev\python.exe -c @'
import asyncio, json, sys
import websockets

async def main():
    sid, token = sys.argv[1], sys.argv[2]
    async with websockets.connect(f"ws://127.0.0.1:8000/terminal/ws/{sid}?token={token}") as ws:
        await ws.send("echo hello")
        print(await asyncio.wait_for(ws.recv(), timeout=10))

asyncio.run(main())
'@ $id $token
```

## 8. 验证 SQLite 持久化

```powershell
C:\Users\bzp20\miniconda3\envs\agent-dev\python.exe -c @'
import sqlite3
conn = sqlite3.connect("devflow.db")
for table in ("sessions", "messages", "tool_calls", "usage"):
    rows = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    print(table, rows)
conn.close()
'@
```

预期 4 张表都有记录（`sessions` ≥ 1、`messages` ≥ 2、`tool_calls` ≥ 1、`usage` ≥ 1）。

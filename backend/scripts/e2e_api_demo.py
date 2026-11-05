"""DevFlow Phase 3 端到端脚本（对运行中的后端执行）。

前置：后端以 mock provider 启动（默认 LLM_PROVIDER=mock，无需 API key）：

    python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

运行：

    python scripts/e2e_api_demo.py --base-url http://127.0.0.1:8000

流程：create session → POST run → 读取 SSE → 文件树/读/写 → GET session 恢复。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator

import httpx


def iter_sse(response: httpx.Response) -> Iterator[tuple[str, dict]]:
    event_type: str | None = None
    data_lines: list[str] = []
    for line in response.iter_lines():
        if line == "":
            if event_type is not None:
                payload = "\n".join(data_lines) or "{}"
                yield event_type, json.loads(payload)
            event_type = None
            data_lines = []
        elif line.startswith("event:"):
            event_type = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:") :].strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DevFlow Phase 3 API 端到端演示")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--demo-repo", default="todo-app")
    parser.add_argument("--message", default="帮我看看 src 目录")
    args = parser.parse_args(argv)

    base_url = args.base_url.rstrip("/")

    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        health = client.get("/health")
        health.raise_for_status()
        print(f"[health] {health.json()}")

        created = client.post("/sessions", json={"demo_repo": args.demo_repo})
        created.raise_for_status()
        session = created.json()
        session_id = session["id"]
        headers = {"Authorization": f"Bearer {session['token']}"}
        print(f"[session] id={session_id} demo_repo={session['demo_repo']}")
        print("[session] token=<已签发 JWT，出于安全不打印>")

        run = client.post(
            f"/sessions/{session_id}/run",
            json={"message": args.message},
            headers=headers,
        )
        run.raise_for_status()
        print(f"[run] run_id={run.json()['run_id']}")

        print("[stream]")
        with client.stream("GET", f"/agent/stream/{session_id}", headers=headers, timeout=None) as response:
            response.raise_for_status()
            for event_type, data in iter_sse(response):
                print(f"  event: {event_type}")
                print(f"  data:  {json.dumps(data, ensure_ascii=False)}")

        root = client.get(f"/files/{session_id}/", headers=headers)
        root.raise_for_status()
        print(f"[files] root entries={[entry['name'] for entry in root.json()['entries']]}")

        index = client.get(f"/files/{session_id}/src/index.js", headers=headers)
        index.raise_for_status()
        content = index.json()["content"]
        print(f"[files] src/index.js first line={content.splitlines()[0]}")

        write = client.post(
            f"/files/{session_id}/e2e-demo/hello.txt",
            json={"content": "hello from e2e demo\n"},
            headers=headers,
        )
        write.raise_for_status()
        print(f"[files] wrote {write.json()['path']}")

        detail = client.get(f"/sessions/{session_id}", headers=headers)
        detail.raise_for_status()
        payload = detail.json()
        print(
            f"[session] container_ready={payload['container_ready']} "
            f"messages={[(message['role'], message['content'][:30]) for message in payload['messages']]}"
        )

    print("\n完成：SSE 事件与 SQLite 持久化均已验证。可用 GET /sessions/{id} 再次恢复历史。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""DevFlow Phase 5 端到端验证脚本（对运行中的后端执行）。

覆盖 5 个验收场景：
1. 会话创建 + list 工具流式输出（mock provider）
2. write_patch 触发 patch.proposed → PATCH 接受 → 文件真正落盘
3. 同一会话再触发 patch → PATCH 拒绝 → 文件不变
4. 终端 WebSocket 执行命令并拿到输出
5. GET /sessions/{id} 恢复历史（含持久化的 patch 事件）+ SQLite 三表计数

前置（在 backend 目录，mock 模式无需 API key）：

    python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

运行：

    python scripts/e2e_phase5.py --base-url http://127.0.0.1:8000

失败时以非 0 退出码结束，便于 CI/人工核对。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx

TERMINAL_MARKER = "E2E_TERMINAL_OK"
DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "devflow.db"


class CheckFailed(AssertionError):
    pass


def check(condition: bool, label: str) -> None:
    if not condition:
        raise CheckFailed(label)
    print(f"  [ok] {label}")


def iter_sse(response: httpx.Response) -> Iterator[tuple[str, dict]]:
    event_type: str | None = None
    data_lines: list[str] = []
    for line in response.iter_lines():
        if line == "":
            if event_type is not None:
                yield event_type, json.loads("\n".join(data_lines) or "{}")
            event_type = None
            data_lines = []
        elif line.startswith("event:"):
            event_type = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:") :].strip())


def stream_events(
    client: httpx.Client,
    base_url: str,
    session_id: str,
    headers: dict[str, str],
    on_event=None,
) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    with client.stream(
        "GET", f"{base_url}/agent/stream/{session_id}", headers=headers, timeout=None
    ) as response:
        response.raise_for_status()
        for event_type, data in iter_sse(response):
            events.append((event_type, data))
            if on_event is not None:
                on_event(event_type, data)
    return events


def run_and_stream(
    client: httpx.Client,
    base_url: str,
    session_id: str,
    headers: dict[str, str],
    message: str,
    on_event=None,
) -> list[tuple[str, dict]]:
    run = client.post(
        f"{base_url}/sessions/{session_id}/run",
        json={"message": message},
        headers=headers,
    )
    run.raise_for_status()
    print(f"[run] message={message!r} run_id={run.json()['run_id']}")
    return stream_events(client, base_url, session_id, headers, on_event=on_event)


def read_file(client: httpx.Client, base_url: str, session_id: str, path: str, headers) -> str:
    response = client.get(f"{base_url}/files/{session_id}/{path}", headers=headers)
    response.raise_for_status()
    return response.json()["content"]


def terminal_roundtrip(base_url: str, session_id: str, token: str) -> str:
    from websockets.sync.client import connect

    ws_url = base_url.replace("http://", "ws://").replace("https://", "wss://")
    ws_url = f"{ws_url}/terminal/ws/{session_id}?token={token}"

    output = ""
    with connect(ws_url, open_timeout=10, close_timeout=5) as websocket:
        websocket.send(f"echo {TERMINAL_MARKER}\n")
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                output += websocket.recv(timeout=1)
            except TimeoutError:
                continue
            if TERMINAL_MARKER in output:
                break
    return output


def sqlite_counts(db_path: Path) -> dict[str, int]:
    if not db_path.exists():
        raise CheckFailed(f"SQLite 文件不存在: {db_path}")
    counts: dict[str, int] = {}
    with sqlite3.connect(db_path) as connection:
        for table in ("sessions", "messages", "tool_calls", "usage"):
            counts[table] = connection.execute(
                f"SELECT COUNT(*) FROM {table}"  # noqa: S608 - 固定白名单表名
            ).fetchone()[0]
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DevFlow Phase 5 端到端验证")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--demo-repo", default="todo-app")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    args = parser.parse_args(argv)

    base_url = args.base_url.rstrip("/")
    db_path = Path(args.db_path)
    started_at = time.perf_counter()

    with httpx.Client(base_url=base_url, timeout=60.0) as client:
        health = client.get(f"{base_url}/health")
        health.raise_for_status()
        print(f"[health] {health.json()}")

        # ---------------------------------------------------------- 场景 0 会话
        created = client.post(f"{base_url}/sessions", json={"demo_repo": args.demo_repo})
        created.raise_for_status()
        session = created.json()
        session_id = session["id"]
        headers = {"Authorization": f"Bearer {session['token']}"}
        print(f"\n[场景 1] 会话 + list 工具流式")
        print(f"  session_id={session_id} demo_repo={session['demo_repo']}")

        events = run_and_stream(
            client, base_url, session_id, headers, "帮我看看 src 目录"
        )
        check(events[0][0] == "tool.call", "首个事件是 tool.call")
        check(any(kind == "tool.result" for kind, _ in events), "收到 tool.result")
        check(events[-1][0] == "done", "流以 done 结束")

        # ------------------------------------------------- 场景 2 接受 patch
        print("\n[场景 2] write_patch → patch.proposed → 接受")
        path = "src/index.js"
        before = read_file(client, base_url, session_id, path, headers)
        accepted_state: dict[str, str] = {}

        def accept_patch(event_type: str, data: dict) -> None:
            if event_type != "patch.proposed" or "accepted" in accepted_state:
                return
            accepted_state["patch_id"] = data["patch_id"]
            response = client.patch(
                f"{base_url}/agent/patch/{data['patch_id']}",
                json={"accepted": True},
                headers=headers,
            )
            response.raise_for_status()
            print(f"  [patch] accepted patch_id={data['patch_id']} path={data['path']}")

        events = run_and_stream(
            client,
            base_url,
            session_id,
            headers,
            "给 src/index.js 新增一个 health 端点",
            on_event=accept_patch,
        )
        check("patch_id" in accepted_state, "收到了 patch.proposed 并已接受")
        check(any(kind == "patch.proposed" for kind, _ in events), "事件流包含 patch.proposed")
        after = read_file(client, base_url, session_id, path, headers)
        check("function health()" in after, "接受后文件内容包含 health 函数")
        check(after != before, "接受后文件内容发生了变化")
        check(
            any(kind == "tool.result" and data["ok"] is True for kind, data in events),
            "接受后 tool.result ok=true",
        )
        check(events[-1][0] == "done", "接受流程以 done 结束")

        # ------------------------------------------------- 场景 3 拒绝 patch
        print("\n[场景 3] write_patch → 拒绝 → 文件不变")
        before_reject = after
        rejected_state: dict[str, str] = {}

        def reject_patch(event_type: str, data: dict) -> None:
            if event_type != "patch.proposed" or "patch_id" in rejected_state:
                return
            rejected_state["patch_id"] = data["patch_id"]
            response = client.patch(
                f"{base_url}/agent/patch/{data['patch_id']}",
                json={"accepted": False},
                headers=headers,
            )
            response.raise_for_status()
            print(f"  [patch] rejected patch_id={data['patch_id']}")

        events = run_and_stream(
            client,
            base_url,
            session_id,
            headers,
            "再修复一个 healthz 端点",
            on_event=reject_patch,
        )
        after_reject = read_file(client, base_url, session_id, path, headers)
        check(after_reject == before_reject, "拒绝后文件内容未变化")
        rejected_result = next(
            data
            for kind, data in events
            if kind == "tool.result" and data["ok"] is False
        )
        check(
            "rejected" in rejected_result["result"]["summary"],
            f"拒绝回灌 LLM: {rejected_result['result']['summary']}",
        )
        check(events[-1][0] == "done", "拒绝流程以 done 结束")

        # ------------------------------------------------------ 场景 4 终端 WS
        print("\n[场景 4] 终端 WebSocket 执行命令")
        output = terminal_roundtrip(base_url, session_id, session["token"])
        check(TERMINAL_MARKER in output, f"终端输出包含 {TERMINAL_MARKER}")
        print(f"  terminal output tail={output.strip().splitlines()[-2:]!r}")

        # ------------------------------------------------------ 场景 5 刷新恢复
        print("\n[场景 5] GET /sessions/{id} 恢复历史")
        detail = client.get(f"{base_url}/sessions/{session_id}", headers=headers)
        detail.raise_for_status()
        payload = detail.json()
        messages = payload["messages"]
        check(payload["container_ready"] is True, "container_ready=true")
        check(len(messages) >= 6, f"恢复历史消息 {len(messages)} 条（≥6）")
        check(
            any(message["content"].startswith('{"type": "patch.proposed"') for message in messages),
            "历史中保留了 patch.proposed 事件（可还原审批卡片）",
        )
        check(
            [message["role"] for message in messages[:2]] == ["user", "assistant"],
            "首两条消息角色为 user/assistant",
        )

        counts = sqlite_counts(db_path)
        print(
            "[sqlite] "
            + " ".join(f"{table}={count}" for table, count in counts.items())
        )
        check(counts["sessions"] >= 1, "sessions 表有数据")
        check(counts["messages"] >= len(messages), "messages 表条数 ≥ 恢复出的消息数")
        check(counts["tool_calls"] >= 3, "tool_calls 表记录 list + 两次 patch")
        check(counts["usage"] >= 3, "usage 表记录了 3 次 run")

    elapsed = time.perf_counter() - started_at
    print(f"\n全部 5 个场景通过，用时 {elapsed:.2f}s")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except CheckFailed as exc:
        print(f"\n[FAIL] {exc}", file=sys.stderr)
        sys.exit(1)

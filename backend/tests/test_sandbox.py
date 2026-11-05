import uuid
from pathlib import Path

import pytest


@pytest.fixture
def container_id(sandbox):
    name = f"session-{uuid.uuid4()}"
    return sandbox.create_session_container(name)


async def test_exec_pwd_points_at_session_workspace(sandbox, container_id):
    exit_code, output = await sandbox.exec(container_id, "pwd")

    assert exit_code == 0
    assert Path(container_id).as_posix() in output.replace("\\", "/")


async def test_exec_echo_hello(sandbox, container_id):
    exit_code, output = await sandbox.exec(container_id, "echo hello")

    assert exit_code == 0
    assert "hello" in output


async def test_exec_returns_nonzero_exit_code(sandbox, container_id):
    exit_code, _ = await sandbox.exec(container_id, "exit 3")

    assert exit_code == 3


async def test_exec_times_out_and_kills_process(sandbox, container_id):
    exit_code, output = await sandbox.exec(container_id, "Start-Sleep -Seconds 2", timeout=0.5)

    assert exit_code != 0
    assert "timed out" in output.lower()


async def test_write_then_read_file(sandbox, container_id):
    sandbox.write_file(container_id, "a.txt", "x")
    assert sandbox.read_file(container_id, "a.txt") == "x"


async def test_write_file_creates_parent_directories(sandbox, container_id):
    sandbox.write_file(container_id, "todo-app/src/check.js", "console.log('ok')")
    assert sandbox.read_file(container_id, "todo-app/src/check.js") == "console.log('ok')"


async def test_list_files_returns_entries(sandbox, container_id):
    sandbox.write_file(container_id, "a.txt", "x")
    sandbox.write_file(container_id, "sub/b.txt", "y")

    entries = sandbox.list_files(container_id, "")
    names = {entry["name"]: entry for entry in entries}

    assert names["a.txt"]["type"] == "file"
    assert names["sub"]["type"] == "dir"
    assert "a.txt" in names
    assert names["a.txt"]["path"] == "a.txt"


async def test_list_files_of_subdirectory(sandbox, container_id):
    sandbox.write_file(container_id, "sub/b.txt", "y")

    entries = sandbox.list_files(container_id, "sub")
    assert [entry["name"] for entry in entries] == ["b.txt"]
    assert entries[0]["path"] == "sub/b.txt"


async def test_exec_rejects_container_outside_root(sandbox, container_id, tmp_path):
    outside = tmp_path.resolve()
    with pytest.raises(ValueError):
        await sandbox.exec(str(outside), "echo hi")

    with pytest.raises(ValueError):
        await sandbox.exec("/etc/passwd", "echo hi")


async def test_file_access_rejects_escaping_paths(sandbox, container_id):
    with pytest.raises(ValueError):
        sandbox.write_file(container_id, "/etc/passwd", "x")
    with pytest.raises(ValueError):
        sandbox.write_file(container_id, "../../outside.txt", "x")
    with pytest.raises(ValueError):
        sandbox.read_file(container_id, "../secret.txt")


async def test_stop_and_remove_cleans_workspace(sandbox, container_id):
    sandbox.write_file(container_id, "a.txt", "x")
    await sandbox.stop_and_remove(container_id)

    assert not Path(container_id).exists()
    # 幂等：再次清理不报错
    await sandbox.stop_and_remove(container_id)


async def test_workspace_ensure_container_is_idempotent(sandbox):
    from app.services.workspace import WorkspaceService

    workspace_service = WorkspaceService(sandbox)
    session_id = str(uuid.uuid4())

    first = await workspace_service.ensure_container(session_id, demo_repo="todo-app")
    second = await workspace_service.ensure_container(session_id, demo_repo="todo-app")

    assert first == second
    assert sandbox.read_file(first, "package.json").startswith("{")

    sandbox.write_file(first, "package.json", '{"edited": true}')
    await workspace_service.ensure_container(session_id, demo_repo="todo-app")

    assert sandbox.read_file(first, "package.json") == '{"edited": true}'

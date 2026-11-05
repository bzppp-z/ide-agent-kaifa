"""4 个 agent 工具的行为测试：真实 SandboxService + 临时 workspace。"""

import uuid

import pytest

from app.services.agent.tools import TOOL_SCHEMAS, ToolRegistry, ToolResult


@pytest.fixture
def container_id(sandbox):
    return sandbox.create_session_container(f"session-{uuid.uuid4()}")


@pytest.fixture
def tools():
    return ToolRegistry()


def _create_files(sandbox, container_id: str) -> None:
    sandbox.write_file(
        container_id,
        "src/routes.js",
        "const router = express.Router();\nrouter.get('/', (req, res) => res.json([]));\n",
    )
    sandbox.write_file(container_id, "src/app.js", "const express = require('express');\n")
    sandbox.write_file(container_id, "src/utils.js", "const handlers = [1, 2];\n")
    sandbox.write_file(container_id, "node_modules/pkg/index.js", "const router = null;\n")
    sandbox.write_file(container_id, "README.md", "# demo\n")


def test_tool_schema_contains_four_openai_functions():
    schemas = ToolRegistry().to_schema()

    assert {schema["function"]["name"] for schema in schemas} == {
        "list",
        "read",
        "search",
        "write_patch",
    }
    for schema in schemas:
        assert schema["type"] == "function"
        assert schema["function"]["parameters"]["type"] == "object"
    assert len(schemas) == len(TOOL_SCHEMAS)


async def test_list_formats_directory_entries(tools, sandbox, container_id):
    _create_files(sandbox, container_id)

    result = await tools.call("list", {"path": "src"}, sandbox, container_id)

    assert result.ok is True
    assert "3 file" in result.summary
    assert "routes.js" in result.details
    assert "utils.js" in result.details
    assert isinstance(result.duration_ms, int)


async def test_list_root_accepts_empty_path(tools, sandbox, container_id):
    _create_files(sandbox, container_id)

    result = await tools.call("list", {"path": ""}, sandbox, container_id)

    assert result.ok is True
    assert "src" in result.details
    assert "README.md" in result.details


async def test_list_missing_directory_returns_not_ok(tools, sandbox, container_id):
    result = await tools.call("list", {"path": "missing"}, sandbox, container_id)

    assert result.ok is False
    assert "not found" in result.summary


async def test_list_rejects_path_traversal(tools, sandbox, container_id):
    with pytest.raises(ValueError):
        await tools.call("list", {"path": "../outside"}, sandbox, container_id)
    with pytest.raises(ValueError):
        await tools.call("list", {"path": "/etc"}, sandbox, container_id)


async def test_read_returns_file_content(tools, sandbox, container_id):
    sandbox.write_file(container_id, "src/a.js", "line1\nline2\n")

    result = await tools.call("read", {"path": "src/a.js"}, sandbox, container_id)

    assert result.ok is True
    assert result.details == "line1\nline2"
    assert "src/a.js" in result.summary


async def test_read_truncates_after_2000_lines(tools, sandbox, container_id):
    content = "\n".join(f"line {index}" for index in range(2500))
    sandbox.write_file(container_id, "big.txt", content)

    result = await tools.call("read", {"path": "big.txt"}, sandbox, container_id)

    assert result.ok is True
    assert result.details.endswith("[truncated]")
    assert "line 1999" in result.details
    assert "line 2000" not in result.details
    assert len(result.details.splitlines()) == 2001
    assert "truncated" in result.summary


async def test_read_missing_file_returns_not_ok(tools, sandbox, container_id):
    result = await tools.call("read", {"path": "nope.txt"}, sandbox, container_id)

    assert result.ok is False
    assert "not found" in result.summary


async def test_read_rejects_absolute_path(tools, sandbox, container_id):
    with pytest.raises(ValueError):
        await tools.call("read", {"path": "C:\\Windows\\win.ini"}, sandbox, container_id)


async def test_search_finds_matches_with_path_and_line(tools, sandbox, container_id):
    _create_files(sandbox, container_id)

    result = await tools.call("search", {"query": "router", "path": "src"}, sandbox, container_id)

    assert result.ok is True
    assert "found 2" in result.summary
    lines = result.details.splitlines()
    assert lines[0].startswith("src/routes.js:1: const router")
    assert lines[1].startswith("src/routes.js:2: router.get")
    assert "node_modules" not in result.details


async def test_search_skips_ignored_directories(tools, sandbox, container_id):
    _create_files(sandbox, container_id)

    result = await tools.call("search", {"query": "router", "path": ""}, sandbox, container_id)

    assert result.ok is True
    assert "node_modules/pkg/index.js" not in result.details
    # src/routes.js 两行 + README.md 不含 router → 只有 2 条
    assert "found 2" in result.summary


async def test_search_falls_back_to_literal_when_regex_invalid(tools, sandbox, container_id):
    _create_files(sandbox, container_id)

    result = await tools.call("search", {"query": "[", "path": "src"}, sandbox, container_id)

    assert result.ok is True
    assert "src/utils.js:1: const handlers = [1, 2];" in result.details
    assert "found 2" in result.summary


async def test_search_limits_matches_to_100(tools, sandbox, container_id):
    sandbox.write_file(container_id, "many.txt", "\n".join(f"hit {index}" for index in range(150)))

    result = await tools.call("search", {"query": "hit", "path": ""}, sandbox, container_id)

    assert result.ok is True
    assert len(result.details.splitlines()) == 100
    assert "truncated" in result.summary


async def test_search_respects_max_depth(tools, sandbox, container_id):
    sandbox.write_file(container_id, "deep/a/b/c/d/e/f/needle.txt", "needle here")

    result = await tools.call("search", {"query": "needle", "path": ""}, sandbox, container_id)

    assert result.ok is True
    assert result.details is None
    assert "found 0" in result.summary


async def test_search_missing_path_returns_not_ok(tools, sandbox, container_id):
    result = await tools.call("search", {"query": "x", "path": "missing"}, sandbox, container_id)

    assert result.ok is False
    assert "not found" in result.summary


async def test_search_rejects_path_traversal(tools, sandbox, container_id):
    with pytest.raises(ValueError):
        await tools.call("search", {"query": "x", "path": "../outside"}, sandbox, container_id)


async def test_write_patch_replaces_unique_match(tools, sandbox, container_id):
    sandbox.write_file(container_id, "src/app.js", "const port = 3000;\n")

    result = await tools.call(
        "write_patch",
        {"path": "src/app.js", "search": "port = 3000", "replace": "port = 8080"},
        sandbox,
        container_id,
    )

    assert result.ok is True
    assert "patched" in result.summary
    assert sandbox.read_file(container_id, "src/app.js") == "const port = 8080;\n"
    assert result.extra["before"] == "const port = 3000;\n"
    assert result.extra["after"] == "const port = 8080;\n"


async def test_write_patch_refuses_when_pattern_not_found(tools, sandbox, container_id):
    sandbox.write_file(container_id, "src/app.js", "const port = 3000;\n")

    result = await tools.call(
        "write_patch",
        {"path": "src/app.js", "search": "not-there", "replace": "x"},
        sandbox,
        container_id,
    )

    assert result.ok is False
    assert "pattern not found" in result.summary
    assert sandbox.read_file(container_id, "src/app.js") == "const port = 3000;\n"


async def test_write_patch_refuses_ambiguous_match(tools, sandbox, container_id):
    sandbox.write_file(container_id, "src/app.js", "port\nport\n")

    result = await tools.call(
        "write_patch",
        {"path": "src/app.js", "search": "port", "replace": "PORT"},
        sandbox,
        container_id,
    )

    assert result.ok is False
    assert "ambiguous match" in result.summary
    assert sandbox.read_file(container_id, "src/app.js") == "port\nport\n"


async def test_write_patch_rejects_path_traversal(tools, sandbox, container_id):
    with pytest.raises(ValueError):
        await tools.call(
            "write_patch",
            {"path": "../outside.txt", "search": "a", "replace": "b"},
            sandbox,
            container_id,
        )


async def test_write_patch_missing_file_returns_not_ok(tools, sandbox, container_id):
    result = await tools.call(
        "write_patch",
        {"path": "nope.txt", "search": "a", "replace": "b"},
        sandbox,
        container_id,
    )

    assert result.ok is False
    assert "not found" in result.summary


async def test_unknown_tool_returns_not_ok(tools, sandbox, container_id):
    result = await tools.call("run", {"command": "ls"}, sandbox, container_id)

    assert isinstance(result, ToolResult)
    assert result.ok is False
    assert "unknown tool" in result.summary

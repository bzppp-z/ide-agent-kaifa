import pytest

from app.core.security import validate_workspace_path


@pytest.mark.parametrize("raw", ["", "   ", "/etc/passwd", "../../etc", "../workspace", "src/..", ".", "C:/Windows", "C:\\Windows", "\\\\server\\share\\file"])
def test_reject_invalid_paths(raw):
    with pytest.raises(ValueError):
        validate_workspace_path(raw)


@pytest.mark.parametrize("raw", ["src", "src/router.ts", "todo-app/src/check.js"])
def test_accept_valid_relative_paths(raw):
    assert validate_workspace_path(raw) == raw


def test_accepts_backslash_separated_relative_path():
    assert validate_workspace_path("todo-app\\src\\check.js") == "todo-app/src/check.js"

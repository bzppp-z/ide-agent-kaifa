"""命令分类与输出截断（纯函数，无 IO 依赖）。

- ``DANGEROUS_PATTERNS``：可能破坏系统或工作区外数据的命令，直接拒绝、不进入审批。
- ``INTERACTIVE_PATTERNS``：需要人工交互的命令，agent 无法响应，直接拒绝。
- ``is_readonly_command``：可免审批直接执行的只读命令（支持 ``&`` / ``;`` / ``|`` 串联）。
- ``truncate_output``：命令输出过长时保留头尾，避免撑爆上下文。

工具层（``agent/tools.py``）只调用这里的判定函数做校验，真正执行由 ``AgentLoop`` 负责。
"""

import re

TRUNCATED_MARKER = "[truncated]"

_FLAGS = re.IGNORECASE | re.MULTILINE

# 只读命令首 token；git 需要额外看子命令。
READONLY_COMMANDS = ("ls", "dir", "pwd", "cat", "type", "head", "tail")
READONLY_GIT_SUBCOMMANDS = ("status", "diff")

# rm 的目标是根目录 / home / 当前目录 / 通配；命中即认为会破坏 workspace 之外的数据。
_ROOT_TARGET = r"(?:/|/\*|~|~/\*|\*|\.|\.\*|\.\.|\.\./\*|\./|c:\\|c:\\\*)"
_ROOT_BOUNDARY = r"(?=\s|$|[;&|])"

DANGEROUS_PATTERNS: list[re.Pattern[str]] = [
    # rm -rf /、rm -rf ~、rm -rf .、rm -rf C:\ 等
    re.compile(rf"\brm\s+(?:-[a-z]+\s+)+{_ROOT_TARGET}{_ROOT_BOUNDARY}", _FLAGS),
    # Windows: del /f /s /q C:\ ...
    re.compile(r"\bdel\s+(?:/[a-z]+\s+)+[a-z]:\\", _FLAGS),
    # Windows: rd /s /q C:\ 或 rmdir
    re.compile(r"\b(?:rd|rmdir)\s+(?:/s\s+)?(?:/q\s+)?[a-z]:\\", _FLAGS),
    # PowerShell: Remove-Item -Recurse -Force 指向根目录 / 盘符
    re.compile(
        r"remove-item\b(?=[^\n;|&]*(?:-recurse|-r\s)[^\n;|&]*)"
        r"[^\n;|&]*(?:[a-z]:\\|(?:^|\s)~(?:\\|[\s;|&]|$)|(?:^|\s)/(?:[\s;|&*]|$))",
        _FLAGS,
    ),
    # 磁盘格式化
    re.compile(r"\bformat\s+(?:[a-z]:|/\w|-\w)", _FLAGS),
    re.compile(r"\bmkfs(?:\.\w+)?\s", _FLAGS),
    # 提权
    re.compile(r"\bsudo\s", _FLAGS),
    re.compile(r"\brunas(?:\.exe)?\b", _FLAGS),
    re.compile(r"start-process\b[^\n;|&]*-verb\s+runas", _FLAGS),
    # 关机 / 重启
    re.compile(r"\bshutdown(?:\.exe)?\b", _FLAGS),
    re.compile(r"\breboot\b", _FLAGS),
    re.compile(r"\brestart-computer\b", _FLAGS),
    re.compile(r"\bstop-computer\b", _FLAGS),
    # 磁盘分区工具
    re.compile(r"\bdiskpart(?:\.exe)?\b", _FLAGS),
    # 注册表删除
    re.compile(r"\breg\s+delete\b", _FLAGS),
    # chmod 777 /
    re.compile(r"\bchmod\s+(?:-r\s+)?777\s+(?:/|/\*)", _FLAGS),
    # curl / wget / iwr 管道执行远程脚本
    re.compile(
        r"(?:curl|wget|iwr|invoke-webrequest)\b[^|;\n]*\|"
        r"\s*(?:sudo\s+)?(?:sh|bash|zsh|iex|invoke-expression|powershell)(?:\s|$)",
        _FLAGS,
    ),
    # fork bomb
    re.compile(r":\s*\(\s*\)\s*\{", _FLAGS),
]

INTERACTIVE_PATTERNS: list[re.Pattern[str]] = [
    # 包管理器初始化向导
    re.compile(r"\b(?:npm|yarn|pnpm)\s+init\b", _FLAGS),
    # 分页器 / 编辑器（允许出现在命令开头或分隔符之后）
    re.compile(r"(?:^|[;&|]\s*)(?:vim|vi|nano|less|more)(?:\s|$)", _FLAGS),
    # 裸 read（需要用户输入值）
    re.compile(r"\bread\s*$", _FLAGS),
    # 裸解释器：没有脚本参数时进入 REPL
    re.compile(r"(?:^|[;&|]\s*)(?:python|python3|py|node)\s*$", _FLAGS),
    # 交互式 rebase
    re.compile(r"\bgit\s+rebase\s+(?:-[a-z]*i[a-z]*|--interactive)\b", _FLAGS),
    # 需要持续交互 / 登录的会话类命令
    re.compile(r"(?:^|[;&|]\s*)ssh(?:\s|$)", _FLAGS),
    re.compile(r"(?:^|[;&|]\s*)top(?:\s|$)", _FLAGS),
]

_SEGMENT_SPLIT = re.compile(r"&&|\|\||;|&|\|")


def _matches(patterns: list[re.Pattern[str]], command: str) -> bool:
    if not isinstance(command, str) or not command.strip():
        return False
    return any(pattern.search(command) for pattern in patterns)


def is_dangerous_command(command: str) -> bool:
    """是否命中危险命令（删除根目录、格式化、提权、管道执行远程脚本等）。"""
    return _matches(DANGEROUS_PATTERNS, command)


def is_interactive_command(command: str) -> bool:
    """是否需要人工交互（编辑器、REPL、初始化向导、登录类命令等）。"""
    return _matches(INTERACTIVE_PATTERNS, command)


def is_readonly_command(command: str) -> bool:
    """整条命令是否只读；串联/管道中的每一段都必须是只读命令。"""
    if not isinstance(command, str) or not command.strip():
        return False

    # 重定向写文件不算只读；``2>&1`` 这类合并输出的写法除外。
    if re.search(r"(?<!\|)>(?!&)", command):
        return False

    segments = [segment.strip() for segment in _SEGMENT_SPLIT.split(command)]
    if not segments:
        return False
    return all(_segment_is_readonly(segment) for segment in segments if segment)


def _segment_is_readonly(segment: str) -> bool:
    tokens = segment.split()
    if not tokens:
        return True

    first = tokens[0].strip("\"'").lower()
    if first == "git":
        subcommand = tokens[1].strip("\"'").lower() if len(tokens) > 1 else ""
        return subcommand in READONLY_GIT_SUBCOMMANDS
    return first in READONLY_COMMANDS


def truncate_output(text: str, head: int = 200, tail: int = 200) -> tuple[str, bool]:
    """保留输出头 ``head`` 行与尾 ``tail`` 行，中间插入截断标记。"""
    if not text:
        return "", False

    head = max(int(head), 0)
    tail = max(int(tail), 0)
    lines = text.splitlines()
    if len(lines) <= head + tail:
        return text, False

    return "\n".join(lines[:head] + [TRUNCATED_MARKER] + lines[-tail:]), True


def classify_command(command: str) -> dict[str, bool]:
    """一次性返回三个判定结果，供工具层直接使用。"""
    return {
        "dangerous": is_dangerous_command(command),
        "interactive": is_interactive_command(command),
        "readonly": is_readonly_command(command),
    }

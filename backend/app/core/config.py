"""应用配置（pydantic-settings）。

字段名与 ``backend/.env.example`` 一一对应。本地模式下使用 SQLite + 宿主机
子进程沙箱；Docker 相关字段保留，切换回 Docker 实现时直接生效。
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_ENV_FILE = _BACKEND_DIR / ".env"

_PLACEHOLDER_API_KEYS = {"", "your-key-here", "your_key_here", "changeme"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # 数据库：本地模式 SQLite；生产切 postgresql+asyncpg://...
    database_url: str = "sqlite+aiosqlite:///./devflow.db"

    # 沙箱：本地模式为宿主机工作目录根；Docker 模式为容器镜像与资源限制
    workspace_root_dir: str = "./workspaces"
    container_image: str = "devflow/sandbox:latest"
    container_limits_cpus: float = 1.0
    container_limits_mem: str = "1g"
    container_limits_pids: int = 256
    container_idle_timeout_min: int = 30

    # LLM
    # mock：本地演示用，无 API key 也能跑通 SSE 全链路；deepseek：真实调用
    llm_provider: str = "mock"
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_model: str = "deepseek-chat"

    # Agent 单次 run 上限
    max_steps_per_run: int = 25
    max_tokens_per_run: int = 200000

    # 项目索引：最多索引文件数与单次检索返回的最大条数
    repo_index_max_files: int = 10000
    repo_index_max_results: int = 50

    # 多文件编辑：单批次最多文件数与单文件写入上限（1MB）
    max_files_per_change_set: int = 10
    max_agent_write_bytes: int = 1048576

    # 每次会话保留的检查点数量上限
    max_checkpoints_per_session: int = 50

    # 认证 / 服务
    jwt_secret: str = "change-me-in-production"
    jwt_expire_hours: int = 24
    backend_port: int = 8000
    cors_origins: str = "http://localhost:5173,http://localhost:5174"

    @property
    def workspace_root_path(self) -> Path:
        return Path(self.workspace_root_dir).expanduser().resolve()

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    def require_deepseek_api_key(self) -> str:
        """调用 agent 前取 API key；未配置时明确报错。启动本身不受影响。"""
        key = self.deepseek_api_key.strip()
        if key.lower() in _PLACEHOLDER_API_KEYS:
            raise RuntimeError(
                "DEEPSEEK_API_KEY is not configured; set it in backend/.env before running the agent"
            )
        return key


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

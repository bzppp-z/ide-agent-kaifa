"""系统提示词应注入当前底层模型名，否则模型被问「你是什么模型」时只能回答不知道。"""

from app.services.agent.loop import AgentLoop, describe_model
from app.services.agent.mock_provider import MockProvider
from app.services.agent.provider import DeepSeekProvider
from app.services.agent.tools import ToolRegistry


def _system_content(model_name: str) -> str:
    messages = AgentLoop()._build_messages("你好", ToolRegistry(), model_name=model_name)
    system = messages[0]
    assert system["role"] == "system"
    return system["content"]


def test_system_prompt_contains_model_name() -> None:
    assert "deepseek-chat" in _system_content("deepseek-chat")


def test_system_prompt_falls_back_when_model_unknown() -> None:
    assert "未知" in _system_content("")


def test_describe_model_uses_provider_model_attribute() -> None:
    provider = DeepSeekProvider(api_key="k", base_url="http://x", model="my-local-llm")
    assert describe_model(provider) == "my-local-llm"


def test_describe_model_marks_mock_provider() -> None:
    assert "Mock" in describe_model(MockProvider())

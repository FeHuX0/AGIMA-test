import httpx
import pytest
from pydantic import SecretStr

from app.assistant import Assistant
from app.config import Settings
from app.schemas import Question, TaskMode


@pytest.mark.asyncio
async def test_openai_compatible_protocol() -> None:
    """Exercise actual HTTP serialization without a network or a paid provider."""
    import json

    def provider(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert str(request.url) == "https://provider.example/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        assert body["model"] == "team-model"
        assert body["max_completion_tokens"] == 1800
        assert body["messages"][0]["role"] == "system"
        assert "продуктовой команды" in body["messages"][0]["content"]
        assert body["messages"][1]["role"] == "user"
        assert "Два разработчика" in body["messages"][1]["content"]
        return httpx.Response(200, json={"choices": [{
            "message": {"content": "## План\n1. Уточнить цель"}, "finish_reason": "stop",
        }]})

    settings = Settings(
        _env_file=None, app_env="test", openai_api_key=SecretStr("test-key"),
        openai_base_url="https://provider.example/v1/", openai_model="team-model",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        result = await Assistant(settings, client).answer(Question(
            question="Как запустить функцию?", context="Два разработчика", mode=TaskMode.PLAN,
        ))
    assert result.text == "## План\n1. Уточнить цель"

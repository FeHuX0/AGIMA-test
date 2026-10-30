import asyncio

import httpx
import pytest
from pydantic import SecretStr
from pytest_mock import AsyncMockType, MockerFixture, MockType

from app.assistant import Assistant, AssistantError
from app.config import Settings
from app.prompts import build_messages
from app.schemas import Question, TaskMode


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, app_env="test", openai_api_key=SecretStr("test-secret"))


@pytest.fixture
def client(mocker: MockerFixture) -> MockType:
    return mocker.Mock(spec=httpx.AsyncClient)


@pytest.mark.asyncio
async def test_returns_model_answer(settings: Settings, client: MockType) -> None:
    client.post.return_value = httpx.Response(200, json={"choices": [{
        "message": {"content": "  ## План\n1. Согласовать цель  "}, "finish_reason": "stop",
    }]})
    result = await Assistant(settings, client).answer(Question(question="Как спланировать релиз?"))
    assert result.text == "## План\n1. Согласовать цель"
    assert result.truncated is False


@pytest.mark.asyncio
async def test_reports_truncated_answer(settings: Settings, client: MockType) -> None:
    client.post.return_value = httpx.Response(200, json={"choices": [{
        "message": {"content": "Часть плана"}, "finish_reason": "length",
    }]})
    assert (await Assistant(settings, client).answer(Question(question="Составь план"))).truncated


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "code", "expected_status"), [
    (401, "provider_auth", 502), (403, "provider_auth", 502),
    (429, "rate_limit", 429), (500, "provider_error", 502), (302, "provider_error", 502),
])
async def test_provider_errors_are_safe(
    settings: Settings, client: MockType, status: int, code: str, expected_status: int,
) -> None:
    client.post.return_value = httpx.Response(status, text="sensitive test-secret")
    with pytest.raises(AssistantError) as error:
        await Assistant(settings, client).answer(Question(question="Составь план"))
    assert (error.value.code, error.value.status) == (code, expected_status)
    assert "test-secret" not in error.value.message


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {}, {"choices": []}, {"choices": [{"message": {"content": "  "}}]},
    {"choices": [{"message": {"content": None}}]}, {"choices": "wrong shape"},
])
async def test_invalid_model_response(settings: Settings, client: MockType, body: dict) -> None:
    client.post.return_value = httpx.Response(200, json=body)
    with pytest.raises(AssistantError) as error:
        await Assistant(settings, client).answer(Question(question="Составь план"))
    assert error.value.code == "invalid_response"


@pytest.mark.asyncio
async def test_timeout(settings: Settings, client: MockType) -> None:
    client.post.side_effect = httpx.ReadTimeout("test-secret")
    with pytest.raises(AssistantError) as error:
        await Assistant(settings, client).answer(Question(question="Составь план"))
    assert (error.value.code, error.value.status) == ("timeout", 504)


@pytest.mark.asyncio
async def test_network_failure(settings: Settings, client: MockType) -> None:
    client.post.side_effect = httpx.ConnectError("test-secret")
    with pytest.raises(AssistantError) as error:
        await Assistant(settings, client).answer(Question(question="Составь план"))
    assert error.value.code == "unavailable"


@pytest.mark.asyncio
async def test_missing_key_never_substitutes_template(settings: Settings, client: MockType) -> None:
    settings.openai_api_key = SecretStr("")
    with pytest.raises(AssistantError) as error:
        await Assistant(settings, client).answer(Question(question="Составь план"))
    assert (error.value.code, error.value.status) == ("not_configured", 503)


@pytest.mark.asyncio
async def test_busy_limit_recovers(settings: Settings, client: MockType, mocker: MockerFixture) -> None:
    settings.ai_max_concurrent_requests = 1
    entered, release = asyncio.Event(), asyncio.Event()

    async def wait(*args: object, **kwargs: object) -> httpx.Response:
        entered.set()
        await release.wait()
        return httpx.Response(200, json={"choices": [{"message": {"content": "Готово"}}]})

    post: AsyncMockType = mocker.AsyncMock(side_effect=wait)
    client.post = post
    assistant = Assistant(settings, client)
    request = Question(question="Помоги команде")
    first = asyncio.create_task(assistant.answer(request))
    await asyncio.wait_for(entered.wait(), 1)
    try:
        with pytest.raises(AssistantError) as error:
            await assistant.answer(request)
        assert (error.value.code, error.value.status) == ("busy", 429)
    finally:
        release.set()
        await first
    assert (await assistant.answer(request)).text == "Готово"


def test_prompt_preserves_context_as_user_data() -> None:
    messages = build_messages(Question(question="Подведи итоги", context="Запуск перенесли.", mode=TaskMode.SUMMARY))
    assert messages[0]["role"] == "system"
    assert "Запуск перенесли" not in messages[0]["content"]
    assert "Запуск перенесли" in messages[-1]["content"]


def test_prompt_bounds_history_without_cutting_pairs() -> None:
    previous = [{"role": role, "content": "x" * 4000} for _ in range(10) for role in ("user", "assistant")]
    result = build_messages(Question(question="Что дальше?"), previous, budget=24000)
    assert sum(len(message["content"]) for message in result) <= 24000
    assert [message["role"] for message in result[1:-1]] == ["user", "assistant"] * ((len(result) - 2) // 2)
    assert result[-1]["content"] == "Что дальше?"


def test_production_rejects_insecure_cookie() -> None:
    settings = Settings(_env_file=None, app_env="production", database_url=SecretStr("postgresql+psycopg://user:password@db/vector"),
                        public_origin="http://localhost", cookie_secure=False)
    with pytest.raises(ValueError, match="HTTPS"):
        settings.validate_runtime()

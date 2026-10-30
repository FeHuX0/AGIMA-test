import asyncio
from dataclasses import dataclass

import httpx

from app.config import Settings
from app.prompts import build_messages
from app.schemas import Question


class AssistantError(Exception):
    def __init__(self, code: str, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass(frozen=True)
class Completion:
    text: str
    truncated: bool = False


class Assistant:
    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self.settings = settings
        self.client = client
        self.slots = asyncio.Semaphore(settings.ai_max_concurrent_requests)

    async def answer(
        self, request: Question, previous: list[dict[str, str]] | None = None,
        *, model: str | None = None,
    ) -> Completion:
        if not self.settings.openai_api_key.get_secret_value().strip():
            raise AssistantError(
                "not_configured",
                "Модель не настроена. Укажите OPENAI_API_KEY на сервере и перезапустите сервис.",
                503,
            )
        try:
            await asyncio.wait_for(self.slots.acquire(), timeout=0.1)
        except TimeoutError as exc:
            raise AssistantError(
                "busy", "Все слоты заняты. Попробуйте через несколько секунд.", 429
            ) from exc
        try:
            return await self._complete(request, previous, model=model)
        finally:
            self.slots.release()

    async def _complete(
        self, request: Question, previous: list[dict[str, str]] | None = None,
        *, model: str | None = None,
    ) -> Completion:
        try:
            # No automatic retries: a retried generation can be billed twice.
            async with asyncio.timeout(self.settings.ai_timeout_seconds):
                response = await self.client.post(
                    f"{self.settings.openai_base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.settings.openai_api_key.get_secret_value()}",
                    },
                    json={
                        "model": model or self.settings.openai_model,
                        "messages": build_messages(request, previous, self.settings.context_character_budget),
                        "max_completion_tokens": self.settings.ai_max_completion_tokens,
                    },
                )
        except (httpx.TimeoutException, TimeoutError) as exc:
            raise AssistantError(
                "timeout", "Модель не успела ответить. Попробуйте ещё раз или сократите вопрос.", 504
            ) from exc
        except httpx.RequestError as exc:
            raise AssistantError(
                "unavailable", "Не удалось связаться с моделью. Попробуйте позже.", 502
            ) from exc
        if response.status_code == 429:
            raise AssistantError(
                "rate_limit", "Достигнут лимит провайдера. Подождите или проверьте квоту API.", 429
            )
        if response.status_code in {401, 403}:
            raise AssistantError(
                "provider_auth", "Провайдер отклонил доступ. Проверьте API-ключ и доступ к модели."
            )
        if response.is_error or response.is_redirect:
            raise AssistantError(
                "provider_error", "Провайдер вернул ошибку. Попробуйте позже или проверьте настройки модели."
            )
        try:
            choice = response.json()["choices"][0]
            message = choice["message"]
            if message.get("refusal") or choice.get("finish_reason") == "content_filter":
                raise AssistantError(
                    "refusal", "Модель не может обработать этот запрос. Попробуйте переформулировать его.", 422
                )
            content = message["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError("Empty completion")
            return Completion(content.strip(), truncated=choice.get("finish_reason") == "length")
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            raise AssistantError(
                "invalid_response", "Модель вернула пустой или некорректный ответ. Попробуйте ещё раз."
            ) from exc

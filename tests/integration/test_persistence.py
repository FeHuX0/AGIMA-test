import asyncio
from datetime import timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select, update

from app.assistant import Assistant
from app.models import Conversation, Generation, LoginSession, Message
from app.worker import claim_job, process_job, recover_expired
from tests.integration.conftest import PASSWORD

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def question(**changes: object) -> dict:
    return {"request_id": str(uuid4()), "question": "Как подготовить релиз?", "mode": "plan", **changes}


async def complete(application: FastAPI, request_id: str, *, fail: bool = False) -> list[dict]:
    captured = []

    def provider(request: httpx.Request) -> httpx.Response:
        import json
        captured.extend(json.loads(request.content)["messages"])
        if fail:
            return httpx.Response(429, json={"error": "provider quota"})
        return httpx.Response(200, json={"choices": [{
            "message": {"content": "## План\n1. Уточнить цель"}, "finish_reason": "stop",
        }]})

    settings = application.state.settings
    job_id = await claim_job(application.state.sessions, settings)
    assert job_id == UUID(request_id)
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as provider_client:
        await process_job(application.state.sessions, Assistant(settings, provider_client), job_id)
    return captured


async def test_question_is_durable_before_provider_call(client: httpx.AsyncClient, application: FastAPI) -> None:
    response = await client.post("/api/ask", json=question(context="Два разработчика"))
    assert response.status_code == 202
    job = response.json()
    assert job["status"] == "pending"
    async with application.state.sessions() as db:
        assert await db.get(Generation, UUID(job["id"])) is not None
        message = await db.scalar(select(Message).where(Message.generation_id == UUID(job["id"])))
        assert message.content == "Как подготовить релиз?"
        assert message.context == "Два разработчика"
    await complete(application, job["id"])
    persisted = (await client.get(f'/api/requests/{job["id"]}')).json()
    assert persisted["status"] == "succeeded"
    assert persisted["answer"] == "## План\n1. Уточнить цель"
    async with application.state.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(Message)) == 2


async def test_continuation_includes_only_its_own_history(client: httpx.AsyncClient, application: FastAPI) -> None:
    first = (await client.post("/api/ask", json=question())).json()
    await complete(application, first["id"])
    second = (await client.post("/api/ask", json=question(
        question="А какие риски?", conversation_id=first["conversation_id"],
    ))).json()
    messages = await complete(application, second["id"])
    assert [message["role"] for message in messages] == ["system", "user", "assistant", "user"]
    assert messages[-1]["content"] == "А какие риски?"
    detail = (await client.get(f'/api/conversations/{first["conversation_id"]}')).json()
    assert len(detail["generations"]) == 2


async def test_concurrent_idempotent_submission_creates_one_job(client: httpx.AsyncClient, application: FastAPI) -> None:
    payload = question()
    responses = await asyncio.gather(*(client.post("/api/ask", json=payload) for _ in range(3)))
    assert {response.status_code for response in responses} == {202}
    assert len({response.json()["id"] for response in responses}) == 1
    async with application.state.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(Generation)) == 1
        assert await db.scalar(select(func.count()).select_from(Conversation)) == 1


async def test_idempotency_key_cannot_be_reused_for_different_question(client: httpx.AsyncClient) -> None:
    payload = question()
    assert (await client.post("/api/ask", json=payload)).status_code == 202
    payload["question"] = "Другой вопрос"
    response = await client.post("/api/ask", json=payload)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "idempotency_conflict"


async def test_other_user_cannot_read_or_delete_conversation(client: httpx.AsyncClient, application: FastAPI) -> None:
    job = (await client.post("/api/ask", json=question())).json()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(application), base_url="http://testserver",
                                 headers={"Origin": "http://testserver"}) as bob:
        account = (await bob.post("/api/auth/login", json={"username": "bob", "password": PASSWORD})).json()
        bob.headers["X-CSRF-Token"] = account["csrf_token"]
        assert (await bob.get(f'/api/requests/{job["id"]}')).status_code == 404
        assert (await bob.get(f'/api/conversations/{job["conversation_id"]}')).status_code == 404
        assert (await bob.delete(f'/api/conversations/{job["conversation_id"]}')).status_code == 404
        assert (await bob.get("/api/conversations")).json() == []


async def test_csrf_and_origin_are_required(client: httpx.AsyncClient) -> None:
    headers = dict(client.headers)
    del client.headers["X-CSRF-Token"]
    assert (await client.post("/api/ask", json=question())).status_code == 403
    client.headers.update(headers)
    assert (await client.post("/api/ask", json=question(), headers={"Origin": "https://attacker.example"})).status_code == 403


async def test_logout_revokes_server_session(client: httpx.AsyncClient, application: FastAPI) -> None:
    raw = client.cookies.get("vector-session")
    response = await client.post("/api/auth/logout")
    assert response.status_code == 204
    client.cookies.set("vector-session", raw, domain="testserver.local", path="/")
    assert (await client.get("/api/auth/me")).status_code == 401
    async with application.state.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(LoginSession)) == 0


async def test_provider_failure_is_saved_and_not_automatically_retried(client: httpx.AsyncClient, application: FastAPI) -> None:
    payload = question()
    job = (await client.post("/api/ask", json=payload)).json()
    await complete(application, job["id"], fail=True)
    repeated = (await client.post("/api/ask", json=payload)).json()
    assert repeated["status"] == "failed"
    assert repeated["error_code"] == "rate_limit"
    assert await claim_job(application.state.sessions, application.state.settings) is None


async def test_interrupted_running_job_is_marked_failed(client: httpx.AsyncClient, application: FastAPI) -> None:
    job = (await client.post("/api/ask", json=question())).json()
    assert await claim_job(application.state.sessions, application.state.settings) == UUID(job["id"])
    async with application.state.sessions.begin() as db:
        await db.execute(update(Generation).where(Generation.id == UUID(job["id"]))
                         .values(lease_until=func.now() - timedelta(seconds=1)))
        await recover_expired(db)
    saved = (await client.get(f'/api/requests/{job["id"]}')).json()
    assert saved["status"] == "failed"
    assert saved["error_code"] == "interrupted"


async def test_clear_cascades_messages_and_requests(client: httpx.AsyncClient, application: FastAPI) -> None:
    job = (await client.post("/api/ask", json=question())).json()
    assert (await client.delete("/api/conversations")).status_code == 409
    await complete(application, job["id"])
    assert (await client.delete("/api/conversations")).status_code == 204
    async with application.state.sessions() as db:
        for model in (Conversation, Generation, Message):
            assert await db.scalar(select(func.count()).select_from(model)) == 0


async def test_user_quota_is_stored_in_database(client: httpx.AsyncClient, application: FastAPI) -> None:
    application.state.settings.requests_per_minute = 1
    assert (await client.post("/api/ask", json=question())).status_code == 202
    response = await client.post("/api/ask", json=question())
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "60"


async def test_global_capacity_is_shared_by_workers(client: httpx.AsyncClient, application: FastAPI) -> None:
    application.state.settings.ai_max_concurrent_requests = 1
    for _ in range(2):
        assert (await client.post("/api/ask", json=question())).status_code == 202
    claimed = await asyncio.gather(*(claim_job(application.state.sessions, application.state.settings) for _ in range(2)))
    assert sum(value is not None for value in claimed) == 1


async def test_request_body_is_bounded(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/ask", content=b"x" * (96 * 1024 + 1), headers={"Content-Type": "application/json"})
    assert response.status_code == 413

import os
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from pydantic import SecretStr

from app.config import Settings
from app.main import create_app
from app.models import User
from app.security import password_hasher
from tests.support import isolated_database

PASSWORD = "test-password-long-enough"


@pytest_asyncio.fixture
async def application() -> AsyncIterator[FastAPI]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to run real PostgreSQL integration tests")
    with isolated_database(url) as database_url:
        settings = Settings(
            _env_file=None, app_env="test", database_url=SecretStr(database_url),
            public_origin="http://testserver", cookie_secure=False,
            openai_api_key=SecretStr("test-provider-key"),
        )
        app = create_app(settings)
        async with app.router.lifespan_context(app):
            async with app.state.sessions.begin() as db:
                for name in ("alice", "bob"):
                    db.add(User(username=name, display_name=name.title(),
                                password_hash=password_hasher.hash(PASSWORD)))
            yield app


@pytest_asyncio.fixture
async def client(application: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(application),
                                 base_url="http://testserver", headers={"Origin": "http://testserver"}) as client:
        result = await client.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
        assert result.status_code == 200
        client.headers["X-CSRF-Token"] = result.json()["csrf_token"]
        yield client

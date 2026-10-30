"""Only launched by an operator running npm run test:e2e."""
import asyncio
import os
import signal
import subprocess
import sys

from pydantic import SecretStr

from app.config import Settings
from app.db import create_engine, session_factory
from app.models import User
from app.security import password_hasher
from tests.support import isolated_database


async def seed(url: str) -> None:
    settings = Settings(_env_file=None, database_url=SecretStr(url))
    engine = create_engine(settings)
    try:
        async with session_factory(engine).begin() as db:
            db.add(User(username="alice", display_name="Алиса",
                        password_hash=password_hasher.hash("test-password-long-enough")))
    finally:
        await engine.dispose()


def main() -> None:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        raise SystemExit("Set TEST_DATABASE_URL to an isolated PostgreSQL vector_test database")
    stopped = False

    def stop(*args: object) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with isolated_database(url) as database_url:
        asyncio.run(seed(database_url))
        env = {**os.environ, "APP_ENV": "test", "DATABASE_URL": database_url,
               "COOKIE_SECURE": "false", "PUBLIC_ORIGIN": "http://127.0.0.1:8765",
               "OPENAI_BASE_URL": "http://127.0.0.1:8767/v1", "OPENAI_API_KEY": "test-only",
               "OPENAI_MODEL": "gpt-4.1-mini",
               "REQUESTS_PER_MINUTE": "1000", "LOGIN_ATTEMPTS_PER_MINUTE": "1000"}
        processes = []
        try:
            for args in (
                ["-m", "uvicorn", "tests.e2e.provider:app", "--host", "127.0.0.1", "--port", "8767"],
                ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8765"],
                ["-m", "app.worker"],
            ):
                processes.append(subprocess.Popen([sys.executable, *args], env=env))
            import time
            while not stopped and all(process.poll() is None for process in processes):
                time.sleep(0.2)
            if not stopped:
                raise SystemExit("An E2E service stopped unexpectedly")
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
            for process in processes:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    main()

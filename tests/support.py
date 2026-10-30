"""Explicit test database lifecycle. Never used by application startup."""
from contextlib import contextmanager
from collections.abc import Iterator
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


@contextmanager
def isolated_database(admin_url: str) -> Iterator[str]:
    root = make_url(admin_url)
    if not root.database or not root.database.startswith("vector_test"):
        raise ValueError("TEST_DATABASE_URL must point to a dedicated database named vector_test*")
    name = "vector_test_" + uuid4().hex
    admin = create_engine(root, isolation_level="AUTOCOMMIT", hide_parameters=True)
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    url = root.set(database=name).render_as_string(hide_password=False)
    try:
        cfg = Config("alembic.ini")
        cfg.attributes["database_url"] = url
        command.upgrade(cfg, "head")
        yield url
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()

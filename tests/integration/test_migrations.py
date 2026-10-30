import os

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from app.db import Base
from app import models
from tests.support import isolated_database

pytestmark = pytest.mark.integration


def test_initial_migration_matches_models_and_can_be_reapplied() -> None:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL for PostgreSQL migration tests")
    with isolated_database(url) as test_url:
        engine = create_engine(test_url, hide_parameters=True)
        cfg = Config("alembic.ini")
        cfg.attributes["database_url"] = test_url
        try:
            with engine.connect() as db:
                differences = compare_metadata(MigrationContext.configure(db), Base.metadata)
                assert differences == []
            command.downgrade(cfg, "base")
            with engine.connect() as db:
                assert "users" not in inspect(db).get_table_names()
            command.upgrade(cfg, "head")
            with engine.connect() as db:
                assert "messages" in inspect(db).get_table_names()
        finally:
            engine.dispose()

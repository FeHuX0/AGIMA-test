from alembic import context
from sqlalchemy import create_engine, pool

from app.config import Settings
from app.db import Base
from app import models  # Register ORM metadata for migration comparison.

config = context.config
url = config.attributes.get("database_url") or Settings().database_url.get_secret_value()


def configure(connection):
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(url=url, target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    existing = config.attributes.get("connection")
    if existing is not None:
        configure(existing)
    else:
        with create_engine(url, poolclass=pool.NullPool, hide_parameters=True).connect() as connection:
            configure(connection)

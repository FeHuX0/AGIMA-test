from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import Settings

SCHEMA_REVISION = "0001"


class Base(DeclarativeBase):
    pass


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url.get_secret_value(), pool_pre_ping=True,
        pool_size=settings.db_pool_size, max_overflow=settings.db_max_overflow,
        pool_timeout=10, hide_parameters=True,
        connect_args={"connect_timeout": 5, "options": "-c statement_timeout=15000"},
    )


def session_factory(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)

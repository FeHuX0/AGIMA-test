import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.config import Settings
from app.db import SCHEMA_REVISION, create_engine, session_factory
from app.errors import AppError
from app.middleware import HttpMiddleware
from app.models import WorkerHeartbeat
from app.routes import router

STATIC_DIR = Path(__file__).parent / "static"
logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        config.validate_runtime()
        engine = create_engine(config)
        application.state.settings = config
        application.state.sessions = session_factory(engine)
        # Bound Argon2 memory/CPU use independently of database connection pools.
        application.state.password_slots = asyncio.Semaphore(2)
        try:
            async with engine.connect() as connection:
                revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
                if revision != SCHEMA_REVISION:
                    raise RuntimeError("Database migrations are out of date; run alembic upgrade head")
            yield
        finally:
            await engine.dispose()

    application = FastAPI(
        title="Вектор · AI для команды", version="2.0.0", lifespan=lifespan,
        docs_url="/docs" if config.app_env != "production" else None,
        redoc_url=None, openapi_url="/openapi.json" if config.app_env != "production" else None,
    )
    application.add_middleware(TrustedHostMiddleware, allowed_hosts=[
        urlsplit(config.public_origin).hostname, "localhost", "127.0.0.1", "testserver",
    ])
    application.add_middleware(HttpMiddleware, settings=config)
    application.include_router(router)

    @application.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            content={"error": {"code": exc.code, "message": exc.message},
                     "request_id": request.state.request_id},
            headers={"Retry-After": "60"} if exc.status == 429 else None,
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={
            "error": {"code": "validation_error", "message": "Проверьте заполнение полей и допустимую длину запроса."},
            "request_id": request.state.request_id,
        })

    @application.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        logger.error("database_error request_id=%s", request.state.request_id)
        return JSONResponse(status_code=503, content={
            "error": {"code": "database_unavailable", "message": "Хранилище временно недоступно. Повторите запрос позже."},
            "request_id": request.state.request_id,
        })

    @application.get("/api/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/api/ready", include_in_schema=False)
    async def ready(request: Request) -> JSONResponse:
        try:
            async with request.app.state.sessions() as db:
                recent = await db.scalar(select(WorkerHeartbeat.id).where(
                    WorkerHeartbeat.seen_at > func.now() - timedelta(seconds=30),
                ).limit(1))
            return JSONResponse({"status": "ready" if recent else "worker_unavailable"}, status_code=200 if recent else 503)
        except SQLAlchemyError:
            return JSONResponse({"status": "database_unavailable"}, status_code=503)

    @application.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return application


app = create_app()

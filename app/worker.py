"""Durable PostgreSQL queue consumer. Run separately: python -m app.worker."""
import asyncio
import logging
import signal
from datetime import UTC, datetime, timedelta
from time import perf_counter
from uuid import UUID, uuid4

import httpx
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.assistant import Assistant, AssistantError
from app.config import Settings
from app.db import SCHEMA_REVISION, create_engine, session_factory
from app.models import Conversation, Generation, LoginSession, Message, RateBucket, WorkerHeartbeat
from app.schemas import Question

logger = logging.getLogger(__name__)


async def recover_expired(db: AsyncSession) -> None:
    # An ambiguous paid request is never automatically sent to the provider again.
    await db.execute(update(Generation).where(
        Generation.status == "running", Generation.lease_until < func.now(),
    ).values(
        status="failed", error_code="interrupted", finished_at=func.now(),
        error_message="Обработка была прервана. Проверьте результат и повторите запрос вручную.",
    ))


async def claim_job(sessions: async_sessionmaker, settings: Settings) -> UUID | None:
    async with sessions.begin() as db:
        await db.execute(text("SELECT pg_advisory_xact_lock(70441002)"))
        await recover_expired(db)
        running = await db.scalar(select(func.count()).select_from(Generation).where(Generation.status == "running"))
        if running >= settings.ai_max_concurrent_requests:
            return None
        job = await db.scalar(select(Generation).where(Generation.status == "pending")
                              .order_by(Generation.created_at, Generation.id)
                              .with_for_update(skip_locked=True).limit(1))
        if job is None:
            return None
        job.status = "running"
        job.started_at = await db.scalar(select(func.clock_timestamp()))
        job.lease_until = job.started_at + timedelta(seconds=settings.ai_timeout_seconds + 30)
        return job.id


async def process_job(sessions: async_sessionmaker, assistant: Assistant, job_id: UUID) -> None:
    started = perf_counter()
    async with sessions() as db:
        job = await db.get(Generation, job_id)
        if not job or job.status != "running":
            return
        previous_jobs = list((await db.scalars(select(Generation).where(
            Generation.conversation_id == job.conversation_id,
            Generation.status == "succeeded", Generation.id != job.id,
        ).order_by(Generation.created_at.desc(), Generation.id.desc()).limit(10))).all())
        previous = []
        for previous_job in reversed(previous_jobs):
            content = previous_job.question
            if previous_job.context:
                content += "\n\nКонтекст от пользователя:\n" + previous_job.context
            previous.extend([{"role": "user", "content": content},
                             {"role": "assistant", "content": previous_job.answer}])
        question = Question(question=job.question, context=job.context, mode=job.mode)
        model = job.model
    try:
        completion = await assistant.answer(question, previous, model=model)
        error = None
    except AssistantError as exc:
        completion = None
        error = exc
    except Exception:
        logger.error("generation_internal_error job_id=%s", job_id)
        completion = None
        error = AssistantError("internal_error", "Не удалось обработать запрос. Попробуйте позже.")
    async with sessions.begin() as db:
        job = await db.scalar(select(Generation).where(Generation.id == job_id).with_for_update())
        if not job or job.status != "running":
            return
        job.finished_at = datetime.now(UTC)
        job.duration_ms = round((perf_counter() - started) * 1000)
        if error:
            job.status, job.error_code, job.error_message = "failed", error.code, error.message
        else:
            job.status, job.answer, job.truncated = "succeeded", completion.text, completion.truncated
            db.add(Message(conversation_id=job.conversation_id, generation_id=job.id,
                           role="assistant", content=completion.text))
        await db.execute(update(Conversation).where(Conversation.id == job.conversation_id)
                         .values(updated_at=func.now()))
    logger.info("generation_finished job_id=%s status=%s", job_id, "failed" if error else "succeeded")


async def heartbeat(sessions: async_sessionmaker, worker_id: UUID) -> None:
    async with sessions.begin() as db:
        await db.execute(insert(WorkerHeartbeat).values(id=worker_id).on_conflict_do_update(
            index_elements=[WorkerHeartbeat.id], set_={"seen_at": func.now()},
        ))
        await recover_expired(db)
        await db.execute(delete(LoginSession).where(LoginSession.expires_at < func.now()))
        await db.execute(delete(RateBucket).where(RateBucket.expires_at < func.now()))
        await db.execute(delete(WorkerHeartbeat).where(WorkerHeartbeat.seen_at < func.now() - timedelta(days=1)))


async def run() -> None:
    settings = Settings()
    settings.validate_runtime(worker=True)
    engine = create_engine(settings)
    sessions = session_factory(engine)
    worker_id = uuid4()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    active: set[asyncio.Task] = set()
    last_heartbeat = 0.0
    try:
        async with engine.connect() as connection:
            revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            if revision != SCHEMA_REVISION:
                raise RuntimeError("Database migrations are out of date")
        async with httpx.AsyncClient(timeout=settings.ai_timeout_seconds, follow_redirects=False) as client:
            assistant = Assistant(settings, client)
            while not stop.is_set():
                try:
                    if loop.time() - last_heartbeat > 5:
                        await heartbeat(sessions, worker_id)
                        last_heartbeat = loop.time()
                    active = {task for task in active if not task.done()}
                    if len(active) < settings.ai_max_concurrent_requests:
                        job_id = await claim_job(sessions, settings)
                        if job_id:
                            task = asyncio.create_task(process_job(sessions, assistant, job_id), name=str(job_id))
                            task.add_done_callback(report_task)
                            active.add(task)
                            continue
                except Exception:
                    logger.error("worker_database_unavailable")
                try:
                    await asyncio.wait_for(stop.wait(), 0.5)
                except TimeoutError:
                    pass
            if active:
                await asyncio.gather(*active, return_exceptions=True)
    finally:
        try:
            async with sessions.begin() as db:
                await db.execute(delete(WorkerHeartbeat).where(WorkerHeartbeat.id == worker_id))
        except Exception:
            logger.error("worker_heartbeat_cleanup_failed")
        await engine.dispose()


def report_task(task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception() is not None:
        logger.error("worker_job_persistence_failed job_id=%s", task.get_name())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(run())

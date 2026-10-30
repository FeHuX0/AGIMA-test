import hashlib
from datetime import UTC, datetime, timedelta

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import AppError
from app.models import RateBucket


async def consume(db: AsyncSession, scope: str, limit: int, seconds: int) -> None:
    # Database time and an atomic UPSERT keep limits consistent across processes.
    epoch = int(await db.scalar(select(func.extract("epoch", func.now()))))
    start = epoch // seconds * seconds
    key = f"{scope}:{start}"
    statement = insert(RateBucket).values(
        key=key, count=1, expires_at=datetime.fromtimestamp(start, UTC) + timedelta(seconds=seconds),
    ).on_conflict_do_update(
        index_elements=[RateBucket.key], set_={"count": RateBucket.count + 1},
    ).returning(RateBucket.count)
    count = await db.scalar(statement)
    if count > limit:
        raise AppError("rate_limit", "Достигнут лимит запросов. Попробуйте позже.", 429)


async def consume_login_limit(request: Request, username: str) -> None:
    client = request.client.host if request.client else "unknown"
    ip_key = hashlib.sha256(client.encode()).hexdigest()
    user_key = hashlib.sha256(username.encode()).hexdigest()
    async with request.app.state.sessions.begin() as db:
        limit = request.app.state.settings.login_attempts_per_minute
        await consume(db, f"login-ip:{ip_key}", limit, 60)
        await consume(db, f"login-user:{user_key}", limit, 60)

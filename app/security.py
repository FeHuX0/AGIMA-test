import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Request
from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool

from app.errors import AppError
from app.models import LoginSession, User

password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
DUMMY_HASH = password_hasher.hash(secrets.token_urlsafe(32))


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def csrf_token(token: str) -> str:
    return hashlib.sha256(("csrf:" + token).encode()).hexdigest()


def verify_password(password: str, encoded: str) -> bool:
    try:
        return password_hasher.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False


@dataclass(frozen=True)
class Identity:
    user_id: UUID
    username: str
    display_name: str
    session_hash: str
    csrf: str


async def identity(request: Request) -> Identity:
    settings = request.app.state.settings
    token = request.cookies.get(settings.session_cookie, "")
    if not token or len(token) > 200:
        raise AppError("unauthorized", "Войдите в рабочее пространство.", 401)
    async with request.app.state.sessions() as db:
        row = (await db.execute(
            select(User, LoginSession).join(LoginSession, LoginSession.user_id == User.id)
            .where(LoginSession.token_hash == token_hash(token),
                   LoginSession.expires_at > func.now(), User.active.is_(True))
        )).first()
    if row is None:
        raise AppError("unauthorized", "Сессия истекла. Войдите снова.", 401)
    user, session = row
    result = Identity(user.id, user.username, user.display_name, session.token_hash, csrf_token(token))
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        supplied = request.headers.get("X-CSRF-Token", "")
        if not secrets.compare_digest(supplied, result.csrf):
            raise AppError("csrf", "Обновите страницу и повторите действие.", 403)
    return result


async def login(request: Request, username: str, password: str) -> tuple[User, str]:
    from app.limits import consume_login_limit

    await consume_login_limit(request, username.lower())
    async with request.app.state.sessions() as db:
        candidate = await db.scalar(select(User).where(User.username == username.lower()))
        encoded = candidate.password_hash if candidate else DUMMY_HASH
    async with request.app.state.password_slots:
        valid = await run_in_threadpool(
            verify_password, password, encoded,
        )
    if not valid or candidate is None:
        raise AppError("invalid_credentials", "Неверный логин или пароль.", 401)
    async with request.app.state.sessions.begin() as db:
        # Serialize with password reset/disable to prevent creating a fresh
        # session using a password that an administrator has just revoked.
        user = await db.scalar(select(User).where(User.id == candidate.id).with_for_update())
        if user is None or not user.active or user.password_hash != encoded:
            raise AppError("invalid_credentials", "Неверный логин или пароль.", 401)
        if password_hasher.check_needs_rehash(user.password_hash):
            async with request.app.state.password_slots:
                user.password_hash = await run_in_threadpool(password_hasher.hash, password)
        token = secrets.token_urlsafe(48)
        # Rotate an existing session rather than accepting a caller-chosen token.
        old = request.cookies.get(request.app.state.settings.session_cookie)
        if old:
            previous = await db.get(LoginSession, token_hash(old))
            if previous:
                await db.delete(previous)
        db.add(LoginSession(
            token_hash=token_hash(token), user_id=user.id,
            expires_at=func.now() + timedelta(hours=request.app.state.settings.session_hours),
        ))
    return user, token

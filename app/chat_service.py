import hashlib
import json
from uuid import UUID

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.errors import AppError
from app.limits import consume
from app.models import Conversation, Generation, Message, User
from app.schemas import AskRequest

ACTIVE = ("pending", "running")


async def owned_chat(db: AsyncSession, user_id: UUID, chat_id: UUID, *, lock: bool = False) -> Conversation:
    query = select(Conversation).where(Conversation.id == chat_id, Conversation.user_id == user_id)
    if lock:
        query = query.with_for_update()
    chat = await db.scalar(query)
    if chat is None:
        raise AppError("not_found", "Диалог не найден.", 404)
    return chat


async def submit(db: AsyncSession, user_id: UUID, payload: AskRequest, config: Settings) -> Generation:
    # Serialize each user's submissions/deletions; avoids idempotency and quota races.
    active_user = await db.scalar(select(User.id).where(User.id == user_id, User.active.is_(True)).with_for_update())
    if active_user is None:
        raise AppError("unauthorized", "Учётная запись недоступна.", 401)
    digest = hashlib.sha256(json.dumps(
        {"question": payload.question, "context": payload.context, "mode": payload.mode,
         "conversation_id": str(payload.conversation_id) if payload.conversation_id else None},
        sort_keys=True, ensure_ascii=False,
    ).encode()).hexdigest()
    existing = await db.get(Generation, payload.request_id)
    if existing:
        if existing.user_id != user_id:
            raise AppError("not_found", "Запрос не найден.", 404)
        if existing.payload_hash != digest:
            raise AppError("idempotency_conflict", "Этот ID уже использован для другого запроса.", 409)
        return existing
    if payload.conversation_id:
        chat = await owned_chat(db, user_id, payload.conversation_id, lock=True)
        active = await db.scalar(select(Generation.id).where(
            Generation.conversation_id == chat.id, Generation.status.in_(ACTIVE),
        ))
        if active:
            raise AppError("conversation_busy", "В этом диалоге уже готовится ответ.", 409)
    else:
        chat = Conversation(user_id=user_id, title=payload.question[:160])
        db.add(chat)
        await db.flush()
    await consume(db, f"ask-minute:{user_id}", config.requests_per_minute, 60)
    await consume(db, f"ask-day:{user_id}", config.requests_per_day, 86400)
    # Queue admission is serialized across API replicas, not only within one worker.
    await db.execute(text("SELECT pg_advisory_xact_lock(70441001)"))
    queued = await db.scalar(select(func.count()).select_from(Generation).where(Generation.status.in_(ACTIVE)))
    if queued >= config.max_pending_requests:
        raise AppError("queue_full", "Очередь заполнена. Попробуйте позже.", 429)
    job = Generation(
        id=payload.request_id, user_id=user_id, conversation_id=chat.id,
        payload_hash=digest, question=payload.question, context=payload.context,
        mode=payload.mode.value, model=config.openai_model,
    )
    db.add(job)
    await db.flush()
    db.add(Message(conversation_id=chat.id, generation_id=job.id,
                   role="user", content=payload.question, context=payload.context))
    chat.updated_at = func.now()
    await db.flush()
    return job


async def clear_chats(db: AsyncSession, user_id: UUID) -> None:
    await db.execute(select(User.id).where(User.id == user_id).with_for_update())
    active = await db.scalar(select(Generation.id).where(
        Generation.user_id == user_id, Generation.status.in_(ACTIVE),
    ).limit(1))
    if active:
        raise AppError("conversation_busy", "Дождитесь завершения текущих запросов.", 409)
    await db.execute(delete(Conversation).where(Conversation.user_id == user_id))

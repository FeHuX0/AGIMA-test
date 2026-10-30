from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import delete, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app import chat_service
from app.errors import AppError
from app.models import Conversation, Generation, LoginSession
from app.schemas import (
    AskRequest, ConversationDetail, ConversationInfo, JobInfo, LoginRequest,
    PublicConfig, RecentRequest, RequestHistory, UserInfo,
)
from app.security import Identity, csrf_token, identity, login

router = APIRouter(prefix="/api")


async def database(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessions() as db:
        yield db


DB = Annotated[AsyncSession, Depends(database)]
CurrentUser = Annotated[Identity, Depends(identity)]


@router.post("/auth/login", response_model=UserInfo)
async def sign_in(payload: LoginRequest, request: Request, response: Response) -> UserInfo:
    user, token = await login(request, payload.username, payload.password)
    settings = request.app.state.settings
    response.set_cookie(
        settings.session_cookie, token, httponly=True, secure=settings.cookie_secure,
        samesite="strict", path="/", max_age=settings.session_hours * 3600,
    )
    return UserInfo(id=user.id, username=user.username, display_name=user.display_name, csrf_token=csrf_token(token))


@router.get("/auth/me", response_model=UserInfo)
async def me(user: CurrentUser) -> UserInfo:
    return UserInfo(id=user.user_id, username=user.username, display_name=user.display_name, csrf_token=user.csrf)


@router.post("/auth/logout", status_code=204)
async def sign_out(request: Request, response: Response, user: CurrentUser, db: DB) -> None:
    async with db.begin():
        await db.execute(delete(LoginSession).where(LoginSession.token_hash == user.session_hash))
    response.delete_cookie(request.app.state.settings.session_cookie, path="/",
                           secure=request.app.state.settings.cookie_secure, httponly=True, samesite="strict")


@router.get("/config", response_model=PublicConfig)
async def public_config(request: Request, user: CurrentUser) -> PublicConfig:
    settings = request.app.state.settings
    return PublicConfig(model=settings.openai_model, ready=bool(settings.openai_api_key.get_secret_value().strip()))


@router.post("/ask", response_model=JobInfo, status_code=202)
async def ask(payload: AskRequest, request: Request, user: CurrentUser, db: DB) -> Generation:
    async with db.begin():
        return await chat_service.submit(db, user.user_id, payload, request.app.state.settings)


@router.get("/requests/{request_id}", response_model=JobInfo)
async def get_request(request_id: UUID, user: CurrentUser, db: DB) -> Generation:
    job = await db.scalar(select(Generation).where(Generation.id == request_id, Generation.user_id == user.user_id))
    if job is None:
        raise AppError("not_found", "Запрос не найден.", 404)
    return job


@router.get("/conversations", response_model=list[ConversationInfo])
async def conversations(user: CurrentUser, db: DB,
                        limit: int = Query(default=5, ge=1, le=100),
                        offset: int = Query(default=0, ge=0, le=100000)) -> list[Conversation]:
    return list((await db.scalars(select(Conversation).where(Conversation.user_id == user.user_id)
                                 .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
                                 .offset(offset).limit(limit))).all())


@router.get("/history", response_model=RequestHistory)
async def request_history(
    user: CurrentUser, db: DB, before: UUID | None = None,
    limit: int = Query(default=5, ge=1, le=100),
) -> RequestHistory:
    query = select(Generation).where(Generation.user_id == user.user_id)
    if before:
        cursor = await db.scalar(select(Generation).where(
            Generation.id == before, Generation.user_id == user.user_id,
        ))
        if cursor is None:
            raise AppError("not_found", "Запрос не найден.", 404)
        query = query.where(tuple_(Generation.created_at, Generation.id) < (cursor.created_at, cursor.id))
    jobs = list((await db.scalars(query.order_by(
        Generation.created_at.desc(), Generation.id.desc(),
    ).limit(limit + 1))).all())
    return RequestHistory(
        items=[RecentRequest.model_validate(job) for job in jobs[:limit]], has_more=len(jobs) > limit,
    )


@router.get("/conversations/{chat_id}", response_model=ConversationDetail)
async def conversation(chat_id: UUID, user: CurrentUser, db: DB,
                       before: UUID | None = None, through: UUID | None = None,
                       limit: int = Query(default=50, ge=1, le=100)) -> ConversationDetail:
    chat = await chat_service.owned_chat(db, user.user_id, chat_id)
    if before and through:
        raise AppError("invalid_cursor", "Укажите только один курсор истории.", 422)
    query = select(Generation).where(Generation.conversation_id == chat.id)
    if before or through:
        cursor = await db.scalar(select(Generation).where(
            Generation.id == (before or through), Generation.conversation_id == chat.id,
        ))
        if cursor is None:
            raise AppError("not_found", "Сообщение не найдено.", 404)
        position = tuple_(Generation.created_at, Generation.id)
        boundary = (cursor.created_at, cursor.id)
        query = query.where(position < boundary if before else position <= boundary)
    jobs = list((await db.scalars(query.order_by(Generation.created_at.desc(), Generation.id.desc()).limit(limit + 1))).all())
    return ConversationDetail(
        **ConversationInfo.model_validate(chat).model_dump(),
        generations=[JobInfo.model_validate(job) for job in reversed(jobs[:limit])], has_more=len(jobs) > limit,
    )


@router.delete("/conversations", status_code=204)
async def clear_conversations(user: CurrentUser, db: DB) -> None:
    async with db.begin():
        await chat_service.clear_chats(db, user.user_id)


@router.delete("/conversations/{chat_id}", status_code=204)
async def delete_conversation(chat_id: UUID, user: CurrentUser, db: DB) -> None:
    from app.models import User

    async with db.begin():
        await db.execute(select(User.id).where(User.id == user.user_id).with_for_update())
        chat = await chat_service.owned_chat(db, user.user_id, chat_id, lock=True)
        active = await db.scalar(select(Generation.id).where(
            Generation.conversation_id == chat.id, Generation.status.in_(chat_service.ACTIVE),
        ).limit(1))
        if active:
            raise AppError("conversation_busy", "Дождитесь завершения запроса.", 409)
        await db.delete(chat)

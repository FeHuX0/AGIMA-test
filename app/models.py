from datetime import datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class User(Base):
    __tablename__ = "users"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    username: Mapped[str] = mapped_column(sa.String(64), unique=True)
    display_name: Mapped[str] = mapped_column(sa.String(80))
    password_hash: Mapped[str] = mapped_column(sa.String(255))
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())


class LoginSession(Base):
    __tablename__ = "login_sessions"
    token_hash: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), index=True)


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(sa.String(160))
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())
    __table_args__ = (sa.Index("ix_conversations_user_updated", "user_id", sa.desc("updated_at"), "id"),)


class Generation(Base):
    __tablename__ = "generations"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[UUID] = mapped_column(sa.ForeignKey("conversations.id", ondelete="CASCADE"))
    payload_hash: Mapped[str] = mapped_column(sa.String(64))
    question: Mapped[str] = mapped_column(sa.Text)
    context: Mapped[str] = mapped_column(sa.Text, default="")
    mode: Mapped[str] = mapped_column(sa.String(16))
    status: Mapped[str] = mapped_column(sa.String(16), default="pending")
    model: Mapped[str] = mapped_column(sa.String(200))
    answer: Mapped[str | None] = mapped_column(sa.Text)
    truncated: Mapped[bool] = mapped_column(default=False)
    duration_ms: Mapped[int] = mapped_column(default=0)
    error_code: Mapped[str | None] = mapped_column(sa.String(40))
    error_message: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    __table_args__ = (
        sa.CheckConstraint("status IN ('pending','running','succeeded','failed')", name="ck_generation_status"),
        sa.CheckConstraint("mode IN ('ask','plan','summary','decision')", name="ck_generation_mode"),
        sa.CheckConstraint("status != 'succeeded' OR answer IS NOT NULL", name="ck_generation_answer"),
        sa.CheckConstraint("status != 'failed' OR (error_code IS NOT NULL AND error_message IS NOT NULL)", name="ck_generation_error"),
        sa.CheckConstraint("status != 'running' OR (started_at IS NOT NULL AND lease_until IS NOT NULL)", name="ck_generation_lease"),
        sa.CheckConstraint("char_length(question) BETWEEN 3 AND 6000 AND char_length(context) <= 12000", name="ck_generation_input"),
        sa.CheckConstraint("duration_ms >= 0", name="ck_generation_duration"),
        sa.Index("ix_generations_queue", "status", "created_at"),
        sa.Index("ix_generations_conversation", "conversation_id", "created_at"),
        sa.Index("uq_generation_active_conversation", "conversation_id", unique=True,
                 postgresql_where=sa.text("status IN ('pending','running')")),
    )


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(sa.ForeignKey("conversations.id", ondelete="CASCADE"))
    generation_id: Mapped[UUID] = mapped_column(sa.ForeignKey("generations.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(sa.String(16))
    content: Mapped[str] = mapped_column(sa.Text)
    context: Mapped[str] = mapped_column(sa.Text, default="")
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())
    __table_args__ = (
        sa.CheckConstraint("role IN ('user','assistant')", name="ck_message_role"),
        sa.UniqueConstraint("generation_id", "role", name="uq_message_generation_role"),
        sa.Index("ix_messages_conversation_created", "conversation_id", "created_at", "id"),
    )


class RateBucket(Base):
    __tablename__ = "rate_buckets"
    key: Mapped[str] = mapped_column(sa.String(150), primary_key=True)
    count: Mapped[int] = mapped_column(default=0)
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), index=True)


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    seen_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())

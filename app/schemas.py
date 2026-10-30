from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TaskMode(StrEnum):
    ASK = "ask"
    PLAN = "plan"
    SUMMARY = "summary"
    DECISION = "decision"


class Question(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    question: str = Field(min_length=3, max_length=6000)
    context: str = Field(default="", max_length=12000)
    mode: TaskMode = TaskMode.ASK


class AskRequest(Question):
    request_id: UUID
    conversation_id: UUID | None = None


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_.@-]+$")
    password: str = Field(min_length=1, max_length=256)


class UserInfo(BaseModel):
    id: UUID
    username: str
    display_name: str
    csrf_token: str


class JobInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    conversation_id: UUID
    question: str
    context: str
    mode: TaskMode
    status: Literal["pending", "running", "succeeded", "failed"]
    model: str
    answer: str | None
    created_at: datetime
    duration_ms: int
    truncated: bool
    error_code: str | None
    error_message: str | None


class ConversationInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime


class RecentRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    conversation_id: UUID
    question: str
    created_at: datetime
    status: Literal["pending", "running", "succeeded", "failed"]


class RequestHistory(BaseModel):
    items: list[RecentRequest]
    has_more: bool


class ConversationDetail(ConversationInfo):
    generations: list[JobInfo]
    has_more: bool = False


class PublicConfig(BaseModel):
    mode: Literal["openai"] = "openai"
    model: str
    ready: bool
    question_limit: int = 6000
    context_limit: int = 12000
    history_limit: int = 5

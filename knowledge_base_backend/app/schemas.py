from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str
    password: str


class DocumentListRequest(BaseModel):
    keyword: str | None = None
    status: str | None = None
    page_num: int = 1
    page_size: int = 10


class DocumentUploadCheckRequest(BaseModel):
    file_name: str


class ChunkUpdateRequest(BaseModel):
    content: str | None = None
    metadata: dict = Field(default_factory=dict)


class CreateSessionRequest(BaseModel):
    title: str | None = None


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class FeedbackRequest(BaseModel):
    feedback: str
    comment: str | None = None


class TrendRequest(BaseModel):
    days: int = Field(default=7, ge=1, le=90)


class ModelConfigPayload(BaseModel):
    model_type: str
    model_name: str
    config: dict = Field(default_factory=dict)
    is_active: bool = True

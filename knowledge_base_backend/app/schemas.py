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


class QueryUnderstandingResult(BaseModel):
    route: str = "out_of_scope"
    confidence: float = 0.0
    is_follow_up: bool = False
    need_context: bool = False
    raw_query: str
    rewrite_query: str = ""
    keywords_hit: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    search_terms: list[str] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list)
    filters: dict = Field(default_factory=dict)
    reason: str = ""
    provider: str = "rules"
    model: str = "rules"
    fallback_used: bool = False

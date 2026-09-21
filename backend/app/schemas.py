import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class CreditSummary(BaseModel):
    monthly_allocation_cents: int
    used_cents: int
    remaining_cents: int
    period_start: datetime


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=10, max_length=128)


class UserOut(BaseModel):
    id: uuid.UUID
    email: EmailStr
    full_name: str
    role: str
    is_active: bool
    status: str = "active"
    permissions: list[str] = []
    monthly_allocation_cents: int = 0
    used_cents: int = 0

    @field_validator("status", mode="before")
    @classmethod
    def _default_status(cls, v: str | None) -> str:
        return v or "active"

    class Config:
        from_attributes = True


class SignupRequest(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=10, max_length=128)


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=10, max_length=128)
    role: str
    monthly_allocation_cents: int | None = None


class UserUpdate(BaseModel):
    full_name: str | None = None
    role: str | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=10, max_length=128)


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    image_url: str | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class ConversationOut(BaseModel):
    id: uuid.UUID
    title: str
    model: str
    updated_at: datetime

    class Config:
        from_attributes = True


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    archived: bool | None = None


class ConversationDetail(ConversationOut):
    messages: list[MessageOut] = []


class ChatRequest(BaseModel):
    conversation_id: uuid.UUID | None = None
    message: str = Field(min_length=1, max_length=32000)
    # `model` remains for older clients. New clients select a purpose-driven
    # route rather than needing to know provider model IDs.
    model: str | None = None
    mode: Literal["auto", "luna", "terra", "sol"] = "auto"
    reasoning_effort: Literal["low", "medium", "high"] | None = None
    use_knowledge_base: bool = False
    attachment_ids: list[uuid.UUID] = []
    # Applied when the turn is routed to image generation or editing.
    size: str | None = None
    quality: str | None = None
    image_model: str | None = None


class RegenerateRequest(BaseModel):
    """Optional route override for a replacement of the latest text answer."""

    mode: Literal["auto", "luna", "terra", "sol"] = "auto"
    reasoning_effort: Literal["low", "medium", "high"] | None = None


class ImageRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    size: str = "1024x1024"
    quality: str = "medium"
    model: str | None = None


class ImageOut(BaseModel):
    id: uuid.UUID
    prompt: str
    url: str
    size: str
    quality: str
    cost_cents: int
    created_at: datetime


class CreditAllocationUpdate(BaseModel):
    monthly_allocation_cents: int = Field(ge=0)


class UsageRow(BaseModel):
    user_id: uuid.UUID
    full_name: str
    email: EmailStr
    role: str
    text_calls: int
    image_calls: int
    prompt_tokens: int
    completion_tokens: int
    cost_cents: int
    allocation_cents: int


class AttachmentOut(BaseModel):
    id: uuid.UUID
    filename: str
    kind: str
    size_bytes: int
    chars_extracted: int
    preview: str | None = None


class FileItem(BaseModel):
    id: uuid.UUID
    filename: str
    mime_type: str
    kind: str
    size_bytes: int
    source_type: str
    conversation_id: uuid.UUID | None = None
    message_id: uuid.UUID | None = None
    created_at: datetime
    download_url: str
    category: str  # uploaded | generated
    thumbnail_url: str | None = None


class FileListResponse(BaseModel):
    items: list[FileItem]
    total: int
    page: int
    per_page: int

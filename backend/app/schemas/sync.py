from __future__ import annotations

from pydantic import BaseModel, Field


class SyncConfigRequest(BaseModel):
    auto_enabled: bool | None = None
    interval_seconds: int | None = Field(default=None, ge=10, le=604800)


class SyncTriggerRequest(BaseModel):
    source_key: str | None = None

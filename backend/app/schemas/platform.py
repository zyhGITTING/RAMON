from __future__ import annotations

from pydantic import BaseModel


class PlatformPayload(BaseModel):
    name: str
    description: str = ""


class PlatformReorderRequest(BaseModel):
    ids: list[int]

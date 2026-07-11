from __future__ import annotations

from pydantic import BaseModel


class LlmServiceCreateRequest(BaseModel):
    name: str
    base_url: str
    api_key: str
    model: str
    enabled: bool = True
    is_default: bool = False
    verify_tls: bool = True


class LlmServiceUpdateRequest(BaseModel):
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    enabled: bool | None = None
    is_default: bool | None = None
    verify_tls: bool | None = None

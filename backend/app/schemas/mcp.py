from __future__ import annotations

from pydantic import BaseModel


class McpTokenRevokeRequest(BaseModel):
    reason: str = ""


class McpTokenExpiryUpdateRequest(BaseModel):
    validity_period: str


class McpExportApplyRequest(BaseModel):
    source_key: str
    reason: str = ""
    validity_period: str = "3m"


class McpExportRequestHandleRequest(BaseModel):
    status: str
    admin_comment: str = ""


class McpExportRequest(BaseModel):
    source_keys: list[str] = []
    bind_ip: bool = False
    validity_period: str = ""

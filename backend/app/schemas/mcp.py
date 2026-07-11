from __future__ import annotations

from pydantic import BaseModel


class McpTokenRevokeRequest(BaseModel):
    reason: str = ""


class McpExportApplyRequest(BaseModel):
    source_key: str
    reason: str = ""


class McpExportRequestHandleRequest(BaseModel):
    status: str
    admin_comment: str = ""


class McpExportRequest(BaseModel):
    source_keys: list[str] = []
    bind_ip: bool = False

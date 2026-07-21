from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DatasourceCreateRequest(BaseModel):
    source_key: str
    source_name: str
    table_name: str
    http_method: str = Field(pattern="^(GET|POST)$")
    api_url: str = ""
    token: str = ""
    platform_id: int | None = None
    description: str = ""
    business_time_field: str = Field(default="", max_length=256)
    searchable_fields: list[str] = []
    quality_rules: dict[str, Any] = {}
    verify_tls: bool = True
    field_labels: dict[str, str] = {}
    request_config: dict[str, Any] = {}
    response_config: dict[str, Any] = {}
    incremental_config: dict[str, Any] | None = None


class DatasourceUpdateRequest(BaseModel):
    source_name: str | None = None
    http_method: str | None = Field(default=None, pattern="^(GET|POST)$")
    api_url: str | None = None
    token: str | None = None
    platform_id: int | None = None
    description: str | None = None
    business_time_field: str | None = Field(default=None, max_length=256)
    searchable_fields: list[str] | None = None
    quality_rules: dict[str, Any] | None = None
    verify_tls: bool | None = None
    field_labels: dict[str, str] | None = None
    request_config: dict[str, Any] | None = None
    response_config: dict[str, Any] | None = None
    incremental_config: dict[str, Any] | None = None


class DatasourceStatusRequest(BaseModel):
    enabled: int = Field(ge=0, le=1)


class DatasourceSyncIntervalRequest(BaseModel):
    interval_seconds: int | None = Field(default=None, ge=10, le=604800)


class DatasourceRollbackRequest(BaseModel):
    sync_version: str


class DatasourceDeleteRequest(BaseModel):
    admin_password: str


class ParseDocRequest(BaseModel):
    service_id: int
    filename: str = ""
    document_text: str = Field(..., min_length=1)


class DatasourceFieldMetadataItem(BaseModel):
    field_name: str = Field(min_length=1, max_length=256)
    field_label: str = Field(default="", max_length=256)
    standard_field_code: str = Field(default="", max_length=128)
    standard_field_name: str = Field(default="", max_length=256)
    business_domain: str = Field(default="", max_length=128)
    definition: str = Field(default="", max_length=2000)
    is_restricted: bool = False
    restricted_access: str = Field(default="hide", pattern="^(hide|mask)$")
    mask_rule: str = Field(default="", max_length=256)


class DatasourceFieldMetadataRequest(BaseModel):
    items: list[DatasourceFieldMetadataItem] = Field(default_factory=list, min_length=1)

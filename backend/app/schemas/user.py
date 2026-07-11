from __future__ import annotations

from pydantic import BaseModel, Field


class UserCreateRequest(BaseModel):
    employee_no: str
    username: str
    full_name: str
    password: str = Field(min_length=8)
    role: str = Field(pattern="^(admin|user)$")
    department: str = ""


class UserPermissionsRequest(BaseModel):
    source_keys: list[str] = []


class UserDepartmentRequest(BaseModel):
    department: str = ""


class UserDeleteRequest(BaseModel):
    admin_password: str


class DepartmentPermissionsRequest(BaseModel):
    department: str = ""
    source_keys: list[str] = []


class UserFieldPermissionsRequest(BaseModel):
    source_key: str
    field_names: list[str] = []


class DepartmentFieldPermissionsRequest(BaseModel):
    department: str = ""
    source_key: str = ""
    field_names: list[str] = []


class FieldRestrictionRequest(BaseModel):
    source_key: str
    field_name: str
    is_restricted: bool = False
    restricted_access: str = Field(default="hide", pattern="^(hide|mask)$")
    mask_rule: str = ""

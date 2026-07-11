from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    employee_no: str
    username: str
    full_name: str
    password: str = Field(min_length=8)


class ChangePasswordRequest(BaseModel):
    old_password: str = ""
    new_password: str = Field(min_length=8)

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

FirmRole = Literal["owner", "admin", "accountant", "clerk", "viewer"]


def _vkn_or_tckn(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if value == "":
        return None
    if not re.fullmatch(r"[0-9]{10,11}", value):
        raise ValueError("tax number must be 10 (VKN) or 11 (TCKN) digits")
    return value


class Membership(BaseModel):
    firm_id: uuid.UUID
    firm_name: str
    role: FirmRole


class MeOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str | None
    phone: str | None
    locale: str
    is_super_admin: bool
    memberships: list[Membership]


class MeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    locale: Literal["tr", "en"] | None = None


class FirmCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=200)
    tax_number: str | None = None

    _tax = field_validator("tax_number")(_vkn_or_tckn)


class FirmUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=2, max_length=200)
    tax_number: str | None = None
    tax_office: str | None = Field(default=None, max_length=120)
    email: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    address: str | None = Field(default=None, max_length=500)

    _tax = field_validator("tax_number")(_vkn_or_tckn)


class SubscriptionOut(BaseModel):
    plan_code: str
    plan_name: str
    status: str
    current_period_end: datetime | None
    max_users: int | None
    max_taxpayers: int | None
    max_documents_per_month: int | None


class FirmOut(BaseModel):
    id: uuid.UUID
    name: str
    tax_number: str | None
    tax_office: str | None
    email: str | None
    phone: str | None
    address: str | None
    status: str
    created_at: datetime
    my_role: FirmRole | None = None
    subscription: SubscriptionOut | None = None


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    full_name: str | None
    role: FirmRole
    status: str
    created_at: datetime


class AuditLogOut(BaseModel):
    id: int
    actor_user_id: uuid.UUID | None
    action: str
    entity_type: str | None
    entity_id: uuid.UUID | None
    metadata: dict
    created_at: datetime

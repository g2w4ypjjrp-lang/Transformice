"""Declarative accounting rules: schema + deterministic evaluator.

conditions = {"all": [Condition...]} or {"any": [Condition...]}
actions    = RuleActions

Example: "Shell receipts → 770.02, VAT deductible, paid in cash"
    {"all": [{"field": "supplier_name", "op": "contains", "value": "shell"}]}
    {"expense_account": "770.02", "payment_method": "cash"}
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ACCOUNT_RE = re.compile(r"^[0-9]{3}(\.[0-9A-Za-z]{1,10}){0,5}$")

RuleField = Literal[
    "supplier_tax_number", "supplier_name", "document_type", "direction", "total_amount",
    "item_description", "vat_rate", "payment_method", "currency",
]
RuleOp = Literal["eq", "neq", "contains", "starts_with", "in", "gt", "gte", "lt", "lte"]


class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: RuleField
    op: RuleOp
    value: str | float | int | list[str] | list[float]


class Conditions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    all: list[Condition] | None = None
    any: list[Condition] | None = None

    @model_validator(mode="after")
    def _one(self) -> "Conditions":
        if not self.all and not self.any:
            raise ValueError("conditions need a non-empty 'all' or 'any' list")
        return self


class RuleActions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expense_account: str | None = None
    vat_account: str | None = None
    payment_method: Literal["cash", "bank", "credit_card", "supplier_credit"] | None = None
    payment_account: str | None = None
    non_deductible: bool | None = None       # KKEG
    vat_deductible: bool | None = None
    description: str | None = Field(default=None, max_length=200)

    @field_validator("expense_account", "vat_account", "payment_account")
    @classmethod
    def _code(cls, v: str | None) -> str | None:
        if v is not None and not ACCOUNT_RE.match(v):
            raise ValueError("invalid account code")
        return v

    @model_validator(mode="after")
    def _non_empty(self) -> "RuleActions":
        if not self.model_dump(exclude_none=True):
            raise ValueError("a rule needs at least one action")
        return self


def norm(s: Any) -> str:
    """Case/diacritic-insensitive comparison key for Turkish text."""
    t = str(s or "").replace("İ", "i").replace("I", "ı").lower()
    t = t.translate(str.maketrans("çğıöşü", "cgiosu"))
    t = unicodedata.normalize("NFKD", t)
    return " ".join("".join(c for c in t if not unicodedata.combining(c)).split())


def _num(v: Any) -> Decimal | None:
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _match_one(cond: Condition, facts: dict[str, Any]) -> bool:
    actual = facts.get(cond.field)
    values = actual if isinstance(actual, list) else [actual]
    for a in values:
        if a is None:
            continue
        if cond.op in ("gt", "gte", "lt", "lte"):
            x, y = _num(a), _num(cond.value)
            if x is None or y is None:
                continue
            if {"gt": x > y, "gte": x >= y, "lt": x < y, "lte": x <= y}[cond.op]:
                return True
        elif cond.op == "in":
            options = cond.value if isinstance(cond.value, list) else [cond.value]
            if norm(a) in {norm(o) for o in options}:
                return True
        else:
            x, y = norm(a), norm(cond.value)
            if (cond.op == "eq" and x == y) or (cond.op == "contains" and y in x) or \
               (cond.op == "starts_with" and x.startswith(y)):
                return True
            if cond.op == "neq" and x != y:
                return True
    return False


def matches(conditions: Conditions, facts: dict[str, Any]) -> bool:
    if conditions.all:
        return all(_match_one(c, facts) for c in conditions.all)
    return any(_match_one(c, facts) for c in conditions.any or [])

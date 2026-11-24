"""Canonical extracted-document model (schema version 1).

Every field is optional: unknown/unreadable values are null — never guessed.
Amounts are Decimal. The same model is produced by the UBL parser, the
Claude extractor and manual corrections.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

DocumentType = Literal[
    "thermal_receipt", "retail_receipt", "purchase_invoice", "sales_invoice",
    "e_invoice", "e_archive_invoice", "expense_slip", "other",
]
PaymentMethod = Literal["cash", "credit_card", "bank", "supplier_credit"]

SCHEMA_VERSION = "1"


class Party(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str | None = None
    tax_number: str | None = None
    tax_office: str | None = None
    address: str | None = None

    @field_validator("tax_number")
    @classmethod
    def _digits(cls, v: str | None) -> str | None:
        if v is None:
            return None
        d = "".join(ch for ch in v if ch.isdigit())
        return d or None


class Line(BaseModel):
    model_config = ConfigDict(extra="ignore")
    description: str | None = None
    quantity: Decimal | None = None
    unit: str | None = None
    unit_price: Decimal | None = None
    discount_amount: Decimal | None = None
    net_amount: Decimal | None = None
    vat_rate: Decimal | None = None
    vat_amount: Decimal | None = None
    total_amount: Decimal | None = None
    withholding_code: str | None = None
    withholding_ratio: Decimal | None = Field(default=None, ge=0, le=1)
    withholding_amount: Decimal | None = None


class VatGroup(BaseModel):
    model_config = ConfigDict(extra="ignore")
    rate: Decimal
    base: Decimal | None = None
    amount: Decimal | None = None


class Withholding(BaseModel):
    model_config = ConfigDict(extra="ignore")
    code: str | None = None
    ratio: Decimal | None = Field(default=None, ge=0, le=1)
    amount: Decimal | None = None


class ExtractedDocument(BaseModel):
    model_config = ConfigDict(extra="ignore")
    document_type: DocumentType | None = None
    direction: Literal["purchase", "sales"] | None = None
    supplier: Party = Field(default_factory=Party)
    buyer: Party = Field(default_factory=Party)
    document_number: str | None = None
    ettn: str | None = None
    document_date: date | None = None
    currency: str | None = "TRY"
    exchange_rate: Decimal | None = None
    payment_method: PaymentMethod | None = None
    lines: list[Line] = Field(default_factory=list)
    vat_breakdown: list[VatGroup] = Field(default_factory=list)
    withholding: Withholding | None = None
    total_net: Decimal | None = None
    total_vat: Decimal | None = None
    total_amount: Decimal | None = None
    notes: str | None = None
    # Per-field confidence 0..1 reported by the extractor (absent for source XML).
    confidence: dict[str, float] = Field(default_factory=dict)

    def to_json(self) -> dict:
        return self.model_dump(mode="json", exclude={"confidence"})


# JSON schema for Claude structured output. Amounts are strings to avoid float
# rounding; they are parsed into Decimal by the model above.
def _nullable(t: dict) -> dict:
    return {"anyOf": [t, {"type": "null"}]}


_S = {"type": "string"}
_PARTY = {
    "type": "object",
    "properties": {k: _nullable(_S) for k in ("name", "tax_number", "tax_office", "address")},
    "required": ["name", "tax_number", "tax_office", "address"],
    "additionalProperties": False,
}
_LINE_FIELDS = ("description", "quantity", "unit", "unit_price", "discount_amount", "net_amount", "vat_rate",
                "vat_amount", "total_amount", "withholding_code", "withholding_ratio", "withholding_amount")
CLAUDE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "document_type": _nullable({"type": "string", "enum": list(DocumentType.__args__)}),
        "supplier": _PARTY,
        "buyer": _PARTY,
        "document_number": _nullable(_S),
        "ettn": _nullable(_S),
        "document_date": _nullable({"type": "string", "description": "YYYY-MM-DD"}),
        "currency": _nullable(_S),
        "payment_method": _nullable({"type": "string", "enum": list(PaymentMethod.__args__)}),
        "lines": {"type": "array", "items": {
            "type": "object",
            "properties": {k: _nullable(_S) for k in _LINE_FIELDS},
            "required": list(_LINE_FIELDS),
            "additionalProperties": False,
        }},
        "vat_breakdown": {"type": "array", "items": {
            "type": "object",
            "properties": {"rate": _S, "base": _nullable(_S), "amount": _nullable(_S)},
            "required": ["rate", "base", "amount"],
            "additionalProperties": False,
        }},
        "withholding": _nullable({
            "type": "object",
            "properties": {"code": _nullable(_S), "ratio": _nullable(_S), "amount": _nullable(_S)},
            "required": ["code", "ratio", "amount"],
            "additionalProperties": False,
        }),
        "total_net": _nullable(_S),
        "total_vat": _nullable(_S),
        "total_amount": _nullable(_S),
        "notes": _nullable(_S),
        "confidence": {
            "type": "object",
            "properties": {k: {"type": "number"} for k in (
                "document_type", "supplier_name", "supplier_tax_number", "document_number",
                "document_date", "vat_breakdown", "total_amount", "lines", "payment_method")},
            "required": ["document_type", "supplier_name", "supplier_tax_number", "document_number",
                         "document_date", "vat_breakdown", "total_amount", "lines", "payment_method"],
            "additionalProperties": False,
        },
    },
    "required": ["document_type", "supplier", "buyer", "document_number", "ettn", "document_date", "currency",
                 "payment_method", "lines", "vat_breakdown", "withholding", "total_net", "total_vat",
                 "total_amount", "notes", "confidence"],
    "additionalProperties": False,
}

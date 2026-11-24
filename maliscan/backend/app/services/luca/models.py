"""Value objects passed to the Luca exporter. Money is always Decimal."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class VoucherLine:
    account_code: str
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")
    description: str | None = None
    quantity: Decimal | None = None


@dataclass(frozen=True)
class Voucher:
    """One accounting voucher (Luca 'fiş'), typically one approved journal entry."""

    source_id: uuid.UUID            # journal_entries.id
    taxpayer_id: uuid.UUID
    voucher_date: date
    lines: tuple[VoucherLine, ...]
    document_number: str | None = None
    document_date: date | None = None
    description: str | None = None
    voucher_type: str = "mahsup"
    currency: str = "TRY"

    @property
    def total_debit(self) -> Decimal:
        return sum((ln.debit for ln in self.lines), Decimal("0"))

    @property
    def total_credit(self) -> Decimal:
        return sum((ln.credit for ln in self.lines), Decimal("0"))

    def content_fingerprint(self) -> str:
        """Stable hash of the accounting content (independent of source id).

        Two vouchers with the same date, document number and line set are
        almost certainly the same receipt booked twice.
        """
        parts = [
            self.voucher_date.isoformat(),
            (self.document_number or "").strip().upper(),
            *sorted(f"{ln.account_code}|{ln.debit:.2f}|{ln.credit:.2f}" for ln in self.lines),
        ]
        return hashlib.sha256("\n".join(parts).encode()).hexdigest()


@dataclass
class Issue:
    severity: str            # "error" blocks export, "warning" does not
    code: str
    message: str
    source_id: uuid.UUID | None = None
    line_no: int | None = None

    def as_dict(self) -> dict:
        return {
            "severity": self.severity, "code": self.code, "message": self.message,
            "source_id": str(self.source_id) if self.source_id else None, "line_no": self.line_no,
        }


@dataclass
class ExportFile:
    file_index: int
    filename: str
    content: bytes
    sha256: str
    voucher_count: int
    row_count: int
    voucher_map: dict[str, str]          # Luca Fiş No → source journal entry id
    total_debit: Decimal
    total_credit: Decimal


@dataclass
class ExportResult:
    template_version: str
    template_verified: bool
    files: list[ExportFile] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    exported_source_ids: list[uuid.UUID] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

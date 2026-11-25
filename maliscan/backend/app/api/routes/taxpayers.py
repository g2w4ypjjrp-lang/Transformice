"""Taxpayers (mükellefler) and their accounting master data:
chart of accounts, account mappings and accounting rules."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, File, Query, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import text

from app.api.deps import ALL_ROLES, BOOKERS, DB, MANAGERS, require_firm_role
from app.api.errors import ApiError, not_found
from app.api.util import audit, update_row
from app.domain.chart_seed import classify, seed_rows
from app.domain.rules import ACCOUNT_RE, Conditions, RuleActions
from app.domain.tax_ids import is_valid_tckn, is_valid_vkn

router = APIRouter(prefix="/firms/{firm_id}/taxpayers", tags=["taxpayers"])

PaymentMethod = Literal["cash", "bank", "credit_card", "supplier_credit"]
_DEFAULT_ACCOUNT_FIELDS = (
    "default_cash_account", "default_bank_account", "default_credit_card_account", "default_supplier_account",
    "default_customer_account", "default_input_vat_account", "default_output_vat_account", "default_expense_account",
)


def _code(v: str | None) -> str | None:
    if v is not None and not ACCOUNT_RE.match(v):
        raise ValueError("invalid account code (e.g. 770.01)")
    return v


# ============================================================================ taxpayers
class TaxpayerBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=2, max_length=250)
    tax_office: str | None = Field(default=None, max_length=120)
    trade_registry_number: str | None = Field(default=None, max_length=60)
    email: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    address: str | None = Field(default=None, max_length=500)
    bookkeeping_basis: Literal["balance_sheet", "operating_account"] | None = None
    fiscal_year_start_month: int | None = Field(default=None, ge=1, le=12)
    locked_until: date | None = None
    default_payment_method: PaymentMethod | None = None
    default_cash_account: str | None = None
    default_bank_account: str | None = None
    default_credit_card_account: str | None = None
    default_supplier_account: str | None = None
    default_customer_account: str | None = None
    default_input_vat_account: str | None = None
    default_output_vat_account: str | None = None
    default_expense_account: str | None = None
    settings: dict | None = None
    is_active: bool | None = None

    @field_validator(*_DEFAULT_ACCOUNT_FIELDS)
    @classmethod
    def _codes(cls, v: str | None) -> str | None:
        return _code(v)


class TaxpayerCreate(TaxpayerBase):
    taxpayer_type: Literal["company", "individual"]
    title: str = Field(min_length=2, max_length=250)
    tax_number: str
    seed_default_chart: bool = True

    @model_validator(mode="after")
    def _tax(self) -> "TaxpayerCreate":
        t = self.tax_number.strip()
        if self.taxpayer_type == "individual":
            if not is_valid_tckn(t):
                raise ValueError("invalid T.C. Kimlik No (11 digits, checksum)")
        elif not (len(t) == 10 and t.isdigit()):
            raise ValueError("VKN must be 10 digits")
        self.tax_number = t
        return self


class TaxpayerUpdate(TaxpayerBase):
    pass


class TaxpayerOut(BaseModel):
    id: uuid.UUID
    taxpayer_type: str
    title: str
    tax_number: str
    tax_office: str | None
    trade_registry_number: str | None
    email: str | None
    phone: str | None
    address: str | None
    bookkeeping_basis: str
    fiscal_year_start_month: int
    locked_until: date | None
    default_payment_method: str
    default_cash_account: str | None
    default_bank_account: str | None
    default_credit_card_account: str | None
    default_supplier_account: str | None
    default_customer_account: str | None
    default_input_vat_account: str | None
    default_output_vat_account: str | None
    default_expense_account: str | None
    settings: dict
    is_active: bool
    created_at: datetime
    warnings: list[str] = []
    stats: dict | None = None


_TP_COLS = ", ".join([
    "id", "taxpayer_type", "title", "tax_number", "tax_office", "trade_registry_number", "email", "phone",
    "address", "bookkeeping_basis", "fiscal_year_start_month", "locked_until", "default_payment_method",
    *_DEFAULT_ACCOUNT_FIELDS, "settings", "is_active", "created_at",
])


def _get_tp(conn, firm_id: uuid.UUID, tp_id: uuid.UUID) -> dict:
    row = conn.execute(
        text(f"SELECT {_TP_COLS} FROM public.taxpayers WHERE firm_id = :f AND id = :t AND deleted_at IS NULL"),
        {"f": firm_id, "t": tp_id},
    ).mappings().one_or_none()
    if row is None:
        raise not_found("taxpayer")
    return dict(row)


def _insert_accounts(conn, firm_id: uuid.UUID, tp_id: uuid.UUID, rows: list[dict]) -> int:
    n = 0
    for r in rows:
        n += conn.execute(
            text("""INSERT INTO public.chart_of_accounts
                      (firm_id, taxpayer_id, code, name, parent_code, account_class, normal_balance, is_postable)
                    VALUES (:f, :t, :code, :name, :parent_code, :account_class, :normal_balance, :is_postable)
                    ON CONFLICT (taxpayer_id, code) DO UPDATE SET name = EXCLUDED.name, is_active = true"""),
            {"f": firm_id, "t": tp_id, **r},
        ).rowcount
    return n


@router.get("", response_model=list[TaxpayerOut], summary="List taxpayers with document counters")
def list_taxpayers(firm_id: uuid.UUID, conn: DB, q: str | None = Query(None, max_length=100),
                   include_inactive: bool = False) -> list[TaxpayerOut]:
    require_firm_role(conn, firm_id, ALL_ROLES)
    rows = conn.execute(
        text(f"""SELECT {', '.join('t.' + c.strip() for c in _TP_COLS.split(','))},
                        (SELECT count(*) FROM public.documents d WHERE d.taxpayer_id = t.id AND d.deleted_at IS NULL) AS docs,
                        (SELECT count(*) FROM public.documents d WHERE d.taxpayer_id = t.id AND d.deleted_at IS NULL
                            AND d.status = 'needs_review') AS pending
                   FROM public.taxpayers t
                  WHERE t.firm_id = :f AND t.deleted_at IS NULL AND (:all OR t.is_active)
                    AND (CAST(:q AS text) IS NULL OR t.title ILIKE '%' || :q || '%' OR t.tax_number LIKE :q || '%')
                  ORDER BY t.title"""),
        {"f": firm_id, "q": q, "all": include_inactive},
    ).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        stats = {"documents": d.pop("docs"), "pending_review": d.pop("pending")}
        out.append(TaxpayerOut(**d, stats=stats))
    return out


@router.post("", response_model=TaxpayerOut, status_code=201, summary="Create taxpayer (owner/admin/accountant)")
def create_taxpayer(firm_id: uuid.UUID, body: TaxpayerCreate, conn: DB) -> TaxpayerOut:
    require_firm_role(conn, firm_id, BOOKERS)
    data = body.model_dump(exclude_none=True, exclude={"seed_default_chart"})
    warnings = []
    if body.taxpayer_type == "company" and not is_valid_vkn(body.tax_number):
        warnings.append("vkn_checksum_mismatch")
    cols = list(data)
    placeholders = [f"CAST(:{c} AS jsonb)" if c == "settings" else f":{c}" for c in cols]
    if "settings" in data:
        import json
        data["settings"] = json.dumps(data["settings"])
    tp_id = conn.execute(
        text(f"""INSERT INTO public.taxpayers (firm_id, created_by, {', '.join(cols)})
                 VALUES (:firm_id, auth.uid(), {', '.join(placeholders)}) RETURNING id"""),
        {"firm_id": firm_id, **data},
    ).scalar_one()
    if body.seed_default_chart:
        _insert_accounts(conn, firm_id, tp_id, seed_rows())
    audit(conn, firm_id, "taxpayer.created", "taxpayer", tp_id)
    return TaxpayerOut(**_get_tp(conn, firm_id, tp_id), warnings=warnings)


@router.get("/{taxpayer_id}", response_model=TaxpayerOut, summary="Taxpayer details")
def get_taxpayer(firm_id: uuid.UUID, taxpayer_id: uuid.UUID, conn: DB) -> TaxpayerOut:
    require_firm_role(conn, firm_id, ALL_ROLES)
    return TaxpayerOut(**_get_tp(conn, firm_id, taxpayer_id))


@router.patch("/{taxpayer_id}", response_model=TaxpayerOut, summary="Update taxpayer settings")
def update_taxpayer(firm_id: uuid.UUID, taxpayer_id: uuid.UUID, body: TaxpayerUpdate, conn: DB) -> TaxpayerOut:
    require_firm_role(conn, firm_id, BOOKERS)
    _get_tp(conn, firm_id, taxpayer_id)
    changes = body.model_dump(exclude_unset=True)
    update_row(conn, "taxpayers", changes, "firm_id = :f AND id = :t", {"f": firm_id, "t": taxpayer_id})
    audit(conn, firm_id, "taxpayer.updated", "taxpayer", taxpayer_id, {"fields": sorted(changes)})
    return TaxpayerOut(**_get_tp(conn, firm_id, taxpayer_id))


@router.delete("/{taxpayer_id}", status_code=204, summary="Archive taxpayer (owner/admin; soft delete)")
def delete_taxpayer(firm_id: uuid.UUID, taxpayer_id: uuid.UUID, conn: DB) -> Response:
    require_firm_role(conn, firm_id, MANAGERS)
    _get_tp(conn, firm_id, taxpayer_id)
    conn.execute(text("UPDATE public.taxpayers SET deleted_at = now(), is_active = false WHERE firm_id = :f AND id = :t"),
                 {"f": firm_id, "t": taxpayer_id})
    audit(conn, firm_id, "taxpayer.archived", "taxpayer", taxpayer_id)
    return Response(status_code=204)


# ============================================================================ chart of accounts
class AccountIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    name: str = Field(min_length=1, max_length=250)
    account_class: Literal["asset", "liability", "equity", "income", "expense", "cost", "memo"] | None = None
    normal_balance: Literal["debit", "credit"] | None = None
    is_postable: bool | None = None
    is_active: bool = True

    @field_validator("code")
    @classmethod
    def _c(cls, v: str) -> str:
        return _code(v)  # type: ignore[return-value]


class AccountUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=250)
    is_postable: bool | None = None
    is_active: bool | None = None


class AccountOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    parent_code: str | None
    account_class: str
    normal_balance: str
    is_postable: bool
    is_active: bool


def _account_row(a: AccountIn) -> dict:
    klass, balance = classify(a.code)
    parent = a.code.rsplit(".", 1)[0] if "." in a.code else None
    return {"code": a.code, "name": a.name, "parent_code": parent,
            "account_class": a.account_class or klass, "normal_balance": a.normal_balance or balance,
            "is_postable": a.is_postable if a.is_postable is not None else ("." in a.code)}


def _ensure_parents(conn, firm_id, tp_id, code: str) -> None:
    """Create missing parent accounts (e.g. 770 for 770.01.003) so the hierarchy check holds."""
    parts = code.split(".")
    for i in range(1, len(parts)):
        parent = ".".join(parts[:i])
        exists = conn.execute(text("SELECT 1 FROM public.chart_of_accounts WHERE taxpayer_id = :t AND code = :c"),
                              {"t": tp_id, "c": parent}).first()
        if not exists:
            _insert_accounts(conn, firm_id, tp_id, [_account_row(AccountIn(code=parent, name=parent, is_postable=False))])


@router.get("/{taxpayer_id}/accounts", response_model=list[AccountOut], summary="Chart of accounts")
def list_accounts(firm_id: uuid.UUID, taxpayer_id: uuid.UUID, conn: DB,
                  postable_only: bool = False) -> list[AccountOut]:
    require_firm_role(conn, firm_id, ALL_ROLES)
    _get_tp(conn, firm_id, taxpayer_id)
    rows = conn.execute(
        text("""SELECT id, code, name, parent_code, account_class, normal_balance, is_postable, is_active
                  FROM public.chart_of_accounts WHERE firm_id = :f AND taxpayer_id = :t
                   AND (NOT :p OR (is_postable AND is_active))
                 ORDER BY string_to_array(code, '.')"""),
        {"f": firm_id, "t": taxpayer_id, "p": postable_only},
    ).mappings().all()
    return [AccountOut(**r) for r in rows]


@router.post("/{taxpayer_id}/accounts", response_model=AccountOut, status_code=201, summary="Add/overwrite account")
def create_account(firm_id: uuid.UUID, taxpayer_id: uuid.UUID, body: AccountIn, conn: DB) -> AccountOut:
    require_firm_role(conn, firm_id, BOOKERS)
    _get_tp(conn, firm_id, taxpayer_id)
    _ensure_parents(conn, firm_id, taxpayer_id, body.code)
    _insert_accounts(conn, firm_id, taxpayer_id, [_account_row(body)])
    if "." in body.code:  # parent can no longer be posted to directly
        conn.execute(text("UPDATE public.chart_of_accounts SET is_postable = false WHERE taxpayer_id = :t AND code = :c"),
                     {"t": taxpayer_id, "c": body.code.rsplit(".", 1)[0]})
    row = conn.execute(
        text("""SELECT id, code, name, parent_code, account_class, normal_balance, is_postable, is_active
                  FROM public.chart_of_accounts WHERE taxpayer_id = :t AND code = :c"""),
        {"t": taxpayer_id, "c": body.code}).mappings().one()
    return AccountOut(**row)


@router.patch("/{taxpayer_id}/accounts/{account_id}", response_model=AccountOut, summary="Update account")
def update_account(firm_id: uuid.UUID, taxpayer_id: uuid.UUID, account_id: uuid.UUID, body: AccountUpdate,
                   conn: DB) -> AccountOut:
    require_firm_role(conn, firm_id, BOOKERS)
    n = update_row(conn, "chart_of_accounts", body.model_dump(exclude_unset=True),
                   "firm_id = :f AND taxpayer_id = :t AND id = :a", {"f": firm_id, "t": taxpayer_id, "a": account_id})
    if not n:
        raise not_found("account")
    row = conn.execute(
        text("""SELECT id, code, name, parent_code, account_class, normal_balance, is_postable, is_active
                  FROM public.chart_of_accounts WHERE id = :a"""), {"a": account_id}).mappings().one()
    return AccountOut(**row)


@router.get("/{taxpayer_id}/accounts/export", summary="Download chart of accounts (Hesap Kodu;Hesap Adı, cp1254 CSV)")
def export_accounts(firm_id: uuid.UUID, taxpayer_id: uuid.UUID, conn: DB) -> Response:
    require_firm_role(conn, firm_id, ALL_ROLES)
    _get_tp(conn, firm_id, taxpayer_id)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(["Hesap Kodu", "Hesap Adı"])
    for code, name in conn.execute(text("""SELECT code, name FROM public.chart_of_accounts
                                           WHERE firm_id = :f AND taxpayer_id = :t AND is_active
                                           ORDER BY string_to_array(code, '.')"""), {"f": firm_id, "t": taxpayer_id}):
        w.writerow([code, name])
    return Response(buf.getvalue().encode("cp1254", errors="replace"), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="hesap_plani.csv"'})


@router.post("/{taxpayer_id}/accounts/copy-to-all",
             summary="Save this chart to every other active taxpayer of the office (adds/updates, never deletes)")
def copy_accounts_to_all(firm_id: uuid.UUID, taxpayer_id: uuid.UUID, conn: DB) -> dict:
    require_firm_role(conn, firm_id, BOOKERS)
    _get_tp(conn, firm_id, taxpayer_id)
    rows = [dict(r) for r in conn.execute(text("""SELECT code, name, parent_code, account_class, normal_balance,
                                                         is_postable FROM public.chart_of_accounts
                                                    WHERE firm_id = :f AND taxpayer_id = :t AND is_active
                                                    ORDER BY array_length(string_to_array(code, '.'), 1)"""),
                                          {"f": firm_id, "t": taxpayer_id}).mappings()]
    targets = [r[0] for r in conn.execute(text("""SELECT id FROM public.taxpayers WHERE firm_id = :f AND id <> :t
                                                   AND deleted_at IS NULL"""), {"f": firm_id, "t": taxpayer_id})]
    for tp in targets:
        _insert_accounts(conn, firm_id, tp, rows)
        conn.execute(text("""UPDATE public.chart_of_accounts c SET is_postable = false
                              WHERE c.taxpayer_id = :t AND EXISTS (SELECT 1 FROM public.chart_of_accounts k
                                    WHERE k.taxpayer_id = :t AND k.parent_code = c.code)"""), {"t": tp})
    audit(conn, firm_id, "chart.copied", "taxpayer", taxpayer_id, {"targets": len(targets), "accounts": len(rows)})
    return {"taxpayers": len(targets), "accounts": len(rows)}


@router.post("/{taxpayer_id}/accounts/seed-default", summary="Add the default TDHP accounts (idempotent)")
def seed_accounts(firm_id: uuid.UUID, taxpayer_id: uuid.UUID, conn: DB) -> dict:
    require_firm_role(conn, firm_id, BOOKERS)
    _get_tp(conn, firm_id, taxpayer_id)
    n = _insert_accounts(conn, firm_id, taxpayer_id, seed_rows())
    return {"upserted": n}


@router.post("/{taxpayer_id}/accounts/import",
             summary="Import chart from CSV/XLSX (columns: Hesap Kodu, Hesap Adı) e.g. Luca hesap planı export")
async def import_accounts(firm_id: uuid.UUID, taxpayer_id: uuid.UUID, conn: DB,
                          file: UploadFile = File(...)) -> dict:
    require_firm_role(conn, firm_id, BOOKERS)
    _get_tp(conn, firm_id, taxpayer_id)
    raw = await file.read(5 * 1024 * 1024 + 1)
    if len(raw) > 5 * 1024 * 1024:
        raise ApiError(413, "file_too_large", "chart file must be ≤ 5 MB")
    rows = _parse_chart_file(file.filename or "", raw)
    accepted, rejected = [], []
    for i, (code, name) in enumerate(rows, start=1):
        code = (code or "").strip()
        name = (name or "").strip()
        if not ACCOUNT_RE.match(code) or not name:
            rejected.append({"row": i, "reason": "invalid code or empty name"})
            continue
        accepted.append(AccountIn(code=code, name=name[:250]))
    accepted.sort(key=lambda a: a.code.count("."))
    codes = {a.code for a in accepted}
    for a in accepted:
        _ensure_parents(conn, firm_id, taxpayer_id, a.code)
        has_children = any(c.startswith(a.code + ".") for c in codes)
        _insert_accounts(conn, firm_id, taxpayer_id,
                         [{**_account_row(a), "is_postable": not has_children and "." in a.code}])
    audit(conn, firm_id, "chart.imported", "taxpayer", taxpayer_id, {"accepted": len(accepted), "rejected": len(rejected)})
    return {"accepted": len(accepted), "rejected": rejected[:100]}


def _parse_chart_file(filename: str, raw: bytes) -> list[tuple[str, str]]:
    name = filename.lower()
    if name.endswith((".xlsx", ".xlsm")):
        import openpyxl
        try:
            wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        except Exception as exc:  # noqa: BLE001
            raise ApiError(422, "invalid_file", "could not read Excel file") from exc
        rows = [tuple("" if v is None else str(v) for v in r[:2]) for r in wb.worksheets[0].iter_rows(values_only=True)]
    else:
        for enc in ("utf-8-sig", "cp1254"):
            try:
                txt = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ApiError(422, "invalid_file", "unsupported text encoding")
        delim = ";" if txt.split("\n", 1)[0].count(";") >= txt.split("\n", 1)[0].count(",") else ","
        rows = [tuple(r[:2]) for r in csv.reader(io.StringIO(txt), delimiter=delim) if r]
    rows = [r for r in rows if len(r) == 2]
    if rows and not ACCOUNT_RE.match(rows[0][0].strip()):
        rows = rows[1:]  # header
    return rows


# ============================================================================ mappings
class MappingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    match_field: Literal["supplier_tax_number", "supplier_name", "item_keyword", "expense_category", "document_type"]
    match_value: str = Field(min_length=1, max_length=200)
    account_code: str
    vat_account_code: str | None = None
    is_non_deductible: bool = False
    vat_deductible: bool = True
    priority: int = Field(default=100, ge=0, le=10000)
    is_active: bool = True

    @field_validator("account_code", "vat_account_code")
    @classmethod
    def _c(cls, v: str | None) -> str | None:
        return _code(v)


class MappingOut(MappingIn):
    model_config = ConfigDict(extra="ignore")
    id: uuid.UUID
    source: str
    created_at: datetime


@router.get("/{taxpayer_id}/mappings", response_model=list[MappingOut], summary="Account mappings")
def list_mappings(firm_id: uuid.UUID, taxpayer_id: uuid.UUID, conn: DB) -> list[MappingOut]:
    require_firm_role(conn, firm_id, ALL_ROLES)
    rows = conn.execute(
        text("""SELECT * FROM public.account_mappings WHERE firm_id = :f AND taxpayer_id = :t
                 ORDER BY priority, match_field, match_value"""), {"f": firm_id, "t": taxpayer_id}).mappings().all()
    return [MappingOut(**r) for r in rows]


@router.post("/{taxpayer_id}/mappings", response_model=MappingOut, status_code=201, summary="Create mapping")
def create_mapping(firm_id: uuid.UUID, taxpayer_id: uuid.UUID, body: MappingIn, conn: DB) -> MappingOut:
    require_firm_role(conn, firm_id, BOOKERS)
    _get_tp(conn, firm_id, taxpayer_id)
    row = conn.execute(
        text("""INSERT INTO public.account_mappings (firm_id, taxpayer_id, match_field, match_value, account_code,
                    vat_account_code, is_non_deductible, vat_deductible, priority, is_active, created_by)
                VALUES (:f, :t, :match_field, :match_value, :account_code, :vat_account_code, :is_non_deductible,
                        :vat_deductible, :priority, :is_active, auth.uid()) RETURNING *"""),
        {"f": firm_id, "t": taxpayer_id, **body.model_dump()}).mappings().one()
    return MappingOut(**row)


@router.put("/{taxpayer_id}/mappings/{mapping_id}", response_model=MappingOut, summary="Replace mapping")
def update_mapping(firm_id: uuid.UUID, taxpayer_id: uuid.UUID, mapping_id: uuid.UUID, body: MappingIn,
                   conn: DB) -> MappingOut:
    require_firm_role(conn, firm_id, BOOKERS)
    n = update_row(conn, "account_mappings", body.model_dump(), "firm_id = :f AND taxpayer_id = :t AND id = :m",
                   {"f": firm_id, "t": taxpayer_id, "m": mapping_id})
    if not n:
        raise not_found("mapping")
    row = conn.execute(text("SELECT * FROM public.account_mappings WHERE id = :m"), {"m": mapping_id}).mappings().one()
    return MappingOut(**row)


@router.delete("/{taxpayer_id}/mappings/{mapping_id}", status_code=204, summary="Delete mapping")
def delete_mapping(firm_id: uuid.UUID, taxpayer_id: uuid.UUID, mapping_id: uuid.UUID, conn: DB) -> Response:
    require_firm_role(conn, firm_id, MANAGERS)
    n = conn.execute(text("DELETE FROM public.account_mappings WHERE firm_id = :f AND taxpayer_id = :t AND id = :m"),
                     {"f": firm_id, "t": taxpayer_id, "m": mapping_id}).rowcount
    if not n:
        raise not_found("mapping")
    return Response(status_code=204)


# ============================================================================ rules
class RuleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    priority: int = Field(default=100, ge=0, le=10000)
    conditions: Conditions
    actions: RuleActions
    stop_processing: bool = True
    is_active: bool = True


class RuleOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    priority: int
    conditions: dict
    actions: dict
    stop_processing: bool
    is_active: bool
    created_at: datetime


def _rule_params(body: RuleIn) -> dict:
    import json
    return {"name": body.name, "description": body.description, "priority": body.priority,
            "conditions": json.dumps(body.conditions.model_dump(exclude_none=True)),
            "actions": json.dumps(body.actions.model_dump(exclude_none=True)),
            "stop_processing": body.stop_processing, "is_active": body.is_active}


@router.get("/{taxpayer_id}/rules", response_model=list[RuleOut], summary="Accounting rules (taxpayer + firm-wide)")
def list_rules(firm_id: uuid.UUID, taxpayer_id: uuid.UUID, conn: DB) -> list[RuleOut]:
    require_firm_role(conn, firm_id, ALL_ROLES)
    rows = conn.execute(
        text("""SELECT id, name, description, priority, conditions, actions, stop_processing, is_active, created_at
                  FROM public.accounting_rules WHERE firm_id = :f AND (taxpayer_id = :t OR taxpayer_id IS NULL)
                 ORDER BY priority, created_at"""), {"f": firm_id, "t": taxpayer_id}).mappings().all()
    return [RuleOut(**r) for r in rows]


@router.post("/{taxpayer_id}/rules", response_model=RuleOut, status_code=201, summary="Create rule")
def create_rule(firm_id: uuid.UUID, taxpayer_id: uuid.UUID, body: RuleIn, conn: DB) -> RuleOut:
    require_firm_role(conn, firm_id, BOOKERS)
    _get_tp(conn, firm_id, taxpayer_id)
    row = conn.execute(
        text("""INSERT INTO public.accounting_rules (firm_id, taxpayer_id, name, description, priority, conditions,
                    actions, stop_processing, is_active, created_by)
                VALUES (:f, :t, :name, :description, :priority, CAST(:conditions AS jsonb), CAST(:actions AS jsonb),
                        :stop_processing, :is_active, auth.uid())
                RETURNING id, name, description, priority, conditions, actions, stop_processing, is_active, created_at"""),
        {"f": firm_id, "t": taxpayer_id, **_rule_params(body)}).mappings().one()
    audit(conn, firm_id, "rule.created", "accounting_rule", row["id"])
    return RuleOut(**row)


@router.put("/{taxpayer_id}/rules/{rule_id}", response_model=RuleOut, summary="Replace rule")
def update_rule(firm_id: uuid.UUID, taxpayer_id: uuid.UUID, rule_id: uuid.UUID, body: RuleIn, conn: DB) -> RuleOut:
    require_firm_role(conn, firm_id, BOOKERS)
    row = conn.execute(
        text("""UPDATE public.accounting_rules SET name = :name, description = :description, priority = :priority,
                    conditions = CAST(:conditions AS jsonb), actions = CAST(:actions AS jsonb),
                    stop_processing = :stop_processing, is_active = :is_active
                 WHERE firm_id = :f AND taxpayer_id = :t AND id = :r
                RETURNING id, name, description, priority, conditions, actions, stop_processing, is_active, created_at"""),
        {"f": firm_id, "t": taxpayer_id, "r": rule_id, **_rule_params(body)}).mappings().one_or_none()
    if row is None:
        raise not_found("rule")
    audit(conn, firm_id, "rule.updated", "accounting_rule", rule_id)
    return RuleOut(**row)


@router.delete("/{taxpayer_id}/rules/{rule_id}", status_code=204, summary="Delete rule (owner/admin)")
def delete_rule(firm_id: uuid.UUID, taxpayer_id: uuid.UUID, rule_id: uuid.UUID, conn: DB) -> Response:
    require_firm_role(conn, firm_id, MANAGERS)
    n = conn.execute(text("DELETE FROM public.accounting_rules WHERE firm_id = :f AND taxpayer_id = :t AND id = :r"),
                     {"f": firm_id, "t": taxpayer_id, "r": rule_id}).rowcount
    if not n:
        raise not_found("rule")
    audit(conn, firm_id, "rule.deleted", "accounting_rule", rule_id)
    return Response(status_code=204)

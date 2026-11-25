"""Deterministic accounting engine: extracted document → draft journal entry.

Account selection order (first hit wins, per field):
  1. accounting rules (priority order; stop_processing honoured)
  2. account mappings: supplier tax number → supplier name → item keyword → document type
  3. taxpayer defaults
AI suggestions are not used to pick accounts. The result is always a DRAFT;
balancing and chart membership are checked here and again by the database.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.domain.rules import Conditions, RuleActions, matches, norm
from app.services.extraction.schema import ExtractedDocument

ENGINE_VERSION = "1.0.0"
CENT = Decimal("0.01")
RECEIPT_TYPES = {"thermal_receipt", "retail_receipt", "expense_slip", "other", None}


@dataclass
class TaxpayerConfig:
    tax_number: str
    default_payment_method: str = "cash"
    default_cash_account: str = "100.01"
    default_bank_account: str = "102.01"
    default_credit_card_account: str = "300.01"
    default_supplier_account: str = "320.01"
    default_customer_account: str = "120.01"
    default_input_vat_account: str = "191.01"
    default_output_vat_account: str = "391.01"
    default_expense_account: str = "770.01"
    settings: dict = field(default_factory=dict)


@dataclass
class Mapping:
    match_field: str
    match_value: str
    account_code: str
    vat_account_code: str | None = None
    is_non_deductible: bool = False
    vat_deductible: bool = True
    priority: int = 100
    taxpayer_specific: bool = True


@dataclass
class Rule:
    name: str
    conditions: dict
    actions: dict
    priority: int = 100
    stop_processing: bool = True


@dataclass
class DraftLine:
    account_code: str
    debit: Decimal = Decimal(0)
    credit: Decimal = Decimal(0)
    description: str | None = None
    vat_rate: Decimal | None = None
    source_note: str | None = None


@dataclass
class JournalDraft:
    entry_date: date
    description: str
    document_number: str | None
    lines: list[DraftLine]
    warnings: list[dict]
    voucher_type: str = "mahsup"
    currency: str = "TRY"
    engine_version: str = ENGINE_VERSION

    @property
    def total_debit(self) -> Decimal:
        return sum((l.debit for l in self.lines), Decimal(0))

    @property
    def total_credit(self) -> Decimal:
        return sum((l.credit for l in self.lines), Decimal(0))


class EngineError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _facts(doc: ExtractedDocument, direction: str) -> dict:
    return {
        "supplier_tax_number": doc.supplier.tax_number,
        "supplier_name": doc.supplier.name,
        "document_type": doc.document_type,
        "direction": direction,
        "total_amount": doc.total_amount,
        "item_description": [l.description for l in doc.lines if l.description],
        "vat_rate": [g.rate for g in doc.vat_breakdown],
        "payment_method": doc.payment_method,
        "currency": doc.currency,
    }


def _apply_rules(rules: list[Rule], facts: dict) -> tuple[dict, list[str]]:
    merged: dict = {}
    hits: list[str] = []
    for r in sorted(rules, key=lambda r: r.priority):
        try:
            cond = Conditions.model_validate(r.conditions)
            acts = RuleActions.model_validate(r.actions).model_dump(exclude_none=True)
        except ValueError:
            continue  # invalid stored rule is ignored (validated on write)
        if matches(cond, facts):
            hits.append(r.name)
            for k, v in acts.items():
                merged.setdefault(k, v)
            if r.stop_processing:
                break
    return merged, hits


def _find_mapping(mappings: list[Mapping], doc: ExtractedDocument) -> Mapping | None:
    ordered = sorted(mappings, key=lambda m: (not m.taxpayer_specific, m.priority))
    sup_tax = doc.supplier.tax_number
    sup_name = norm(doc.supplier.name)
    for m in ordered:
        if m.match_field == "supplier_tax_number" and sup_tax and m.match_value.strip() == sup_tax:
            return m
    for m in ordered:
        if m.match_field == "supplier_name" and sup_name and norm(m.match_value) in sup_name:
            return m
    items = [norm(l.description) for l in doc.lines if l.description]
    for m in ordered:
        if m.match_field == "item_keyword" and any(norm(m.match_value) in it for it in items):
            return m
    for m in ordered:
        if m.match_field == "document_type" and doc.document_type and m.match_value == doc.document_type:
            return m
    return None


# Built-in expense categories used only when no rule or mapping matched.
# (keywords are ASCII-folded; account must exist in the taxpayer's chart)
CATEGORY_KEYWORDS: list[tuple[str, str, tuple[str, ...]]] = [
    ("akaryakıt/taşıt", "770.02", ("AKARYAKIT", "PETROL", "BENZIN", "MOTORIN", "KURSUNSUZ", "DIZEL", "LPG", "OPET",
                                    "SHELL", "AYTEMIZ", "OTOPARK", "HGS", "OGS", "OTOYOL", "KOPRU", "TAKSI")),
    ("yemek/ikram", "770.04", ("RESTORAN", "RESTAURANT", "LOKANTA", "CAFE", "KAFE", "YEMEK", "KEBAP", "PIDE",
                               "PIZZA", "BURGER", "SIMIT", "PASTANE", "FIRIN", "EKMEK", "GIDA", "MARKET", "CAY")),
    ("kırtasiye/büro", "770.03", ("KIRTASIYE", "TONER", "KAGIT", "FOTOKOPI", "KALEM", "DOSYA", "KARTUS")),
    ("haberleşme", "770.05", ("TURKCELL", "VODAFONE", "TELEKOM", "TTNET", "SUPERONLINE", "INTERNET", "GSM",
                              "KARGO", "PTT")),
    ("enerji", "770.07", ("ELEKTRIK", "ENERJISA", "DOGALGAZ", "IGDAS", "BASKENTGAZ", "SU IDARESI", "ASKI", "ISKI")),
    ("bakım/onarım", "770.08", ("SERVIS", "YEDEK PARCA", "TAMIR", "BAKIM", "LASTIK", "NALBUR", "HIRDAVAT")),
    ("temizlik", "770.09", ("TEMIZLIK", "DETERJAN", "CAMASIR", "BULASIK")),
    ("yazılım/abonelik", "770.11", ("YAZILIM", "LISANS", "ABONELIK", "GOOGLE", "MICROSOFT", "ADOBE")),
]


def _fold(s: str | None) -> str:
    import unicodedata as _u
    t = (s or "").replace("ı", "i").replace("İ", "I").upper()
    t = _u.normalize("NFKD", t)
    return " ".join("".join(c for c in t if not _u.combining(c)).split())


def _category_account(doc: ExtractedDocument, chart: set[str] | None) -> tuple[str, str] | None:
    """Pick an expense account from the supplier name, then the largest item lines."""
    texts = [_fold(doc.supplier.name)] + [_fold(l.description) for l in sorted(
        doc.lines, key=lambda l: l.total_amount or l.net_amount or Decimal(0), reverse=True)]
    for text in texts:
        if not text:
            continue
        for label, account, words in CATEGORY_KEYWORDS:
            if any(w in text for w in words) and (chart is None or account in chart):
                return label, account
    return None


def _vat_account(cfg: TaxpayerConfig, rate: Decimal, chart: set[str] | None, output: bool) -> str:
    key = "output_vat_accounts" if output else "input_vat_accounts"
    configured = (cfg.settings.get(key) or {}).get(str(int(rate)) if rate == rate.to_integral() else str(rate))
    if configured:
        return configured
    candidate = f"{'391' if output else '191'}.{int(rate):02d}"
    if chart is None or candidate in chart:
        return candidate
    return cfg.default_output_vat_account if output else cfg.default_input_vat_account


def generate_journal(doc: ExtractedDocument, cfg: TaxpayerConfig, *, mappings: list[Mapping] | None = None,
                     rules: list[Rule] | None = None, chart: set[str] | None = None) -> JournalDraft:
    warnings: list[dict] = []

    def warn(code: str, message: str) -> None:
        warnings.append({"code": code, "message": message})

    if doc.document_date is None:
        raise EngineError("date_missing", "cannot create a journal without a document date")
    if doc.total_amount is None or doc.total_amount <= 0:
        raise EngineError("total_missing", "cannot create a journal without a positive total")
    if (doc.currency or "TRY") != "TRY":
        raise EngineError("currency_unsupported", "foreign-currency documents must be booked manually for now")

    direction = doc.direction or ("sales" if doc.document_type == "sales_invoice" else "purchase")
    if doc.buyer.tax_number and doc.supplier.tax_number == cfg.tax_number:
        direction = "sales"
    facts = _facts(doc, direction)
    actions, rule_hits = _apply_rules(rules or [], facts)
    mapping = _find_mapping(mappings or [], doc)
    source = f"rule:{rule_hits[0]}" if rule_hits else (f"mapping:{mapping.match_field}" if mapping else "default")

    non_deductible = actions.get("non_deductible", mapping.is_non_deductible if mapping else False)
    vat_deductible = actions.get("vat_deductible", mapping.vat_deductible if mapping else True) and not non_deductible
    kkeg_account = cfg.settings.get("kkeg_account", "689.01")

    # --- VAT groups (only explicitly known data; never invented) --------------------
    groups = [(g.rate, g.base, g.amount) for g in doc.vat_breakdown if g.base is not None and g.amount is not None]
    if not groups:
        groups = [(None, doc.total_amount, Decimal(0))]
        warn("vat_unknown", "VAT breakdown unknown — whole amount booked without VAT deduction")
    vat_total = sum((a for _, _, a in groups), Decimal(0))
    withholding = (doc.withholding.amount or Decimal(0)) if doc.withholding else Decimal(0)

    description = " - ".join(x for x in (doc.supplier.name if direction == "purchase" else doc.buyer.name,
                                         doc.document_number) if x)[:200] or "Belge"
    lines: list[DraftLine] = []

    if direction == "purchase":
        expense = actions.get("expense_account") or (mapping.account_code if mapping else None)
        if expense is None and not non_deductible:
            category = _category_account(doc, chart)
            if category:
                expense, source = category[1], f"category:{category[0]}"
                warn("account_by_category", f"Gider hesabı belge içeriğinden seçildi ({category[0]} → {category[1]}); "
                                            "kontrol edin.")
        expense = expense or cfg.default_expense_account
        if non_deductible and not (actions.get("expense_account") or mapping):
            expense = kkeg_account
        for rate, base, amount in groups:
            label = f"KDV %{rate.normalize()}" if rate is not None else None
            if vat_deductible and amount > 0:
                lines.append(DraftLine(expense, debit=base, description=description, vat_rate=rate, source_note=source))
                vat_acc = actions.get("vat_account") or (mapping.vat_account_code if mapping else None) \
                    or _vat_account(cfg, rate, chart, output=False)
                lines.append(DraftLine(vat_acc, debit=amount, description=label, vat_rate=rate, source_note="vat"))
            else:
                lines.append(DraftLine(expense, debit=base + amount, description=description, vat_rate=rate,
                                       source_note=source + (":kkeg" if non_deductible else ":vat_not_deducted")))
        if non_deductible:
            warn("non_deductible", "booked as non-deductible (KKEG); VAT not deducted")

        if withholding > 0:
            if not vat_deductible:
                raise EngineError("withholding_with_kkeg", "VAT withholding on a non-deductible expense needs manual booking")
            wh_acc = cfg.settings.get("withholding_vat_account", "360.02")
            lines.append(DraftLine(wh_acc, credit=withholding, description="KDV tevkifatı", source_note="withholding"))

        method = actions.get("payment_method") or doc.payment_method or (
            cfg.default_payment_method if doc.document_type in RECEIPT_TYPES else "supplier_credit")
        pay_acc = actions.get("payment_account") or {
            "cash": cfg.default_cash_account, "bank": cfg.default_bank_account,
            "credit_card": cfg.default_credit_card_account, "supplier_credit": cfg.default_supplier_account,
        }[method]
        payable = sum((l.debit for l in lines), Decimal(0)) - withholding
        lines.append(DraftLine(pay_acc, credit=payable, description=description, source_note=f"payment:{method}"))
        expected = doc.total_amount - withholding
    else:
        income = actions.get("expense_account") or (mapping.account_code if mapping else None) \
            or cfg.settings.get("sales_account", "600.01")
        method = actions.get("payment_method") or doc.payment_method or "supplier_credit"
        recv_acc = actions.get("payment_account") or {
            "cash": cfg.default_cash_account, "bank": cfg.default_bank_account,
            "credit_card": cfg.default_bank_account, "supplier_credit": cfg.default_customer_account,
        }[method]
        credits: list[DraftLine] = []
        for rate, base, amount in groups:
            credits.append(DraftLine(income, credit=base, description=description, vat_rate=rate, source_note=source))
            if amount > 0:
                credits.append(DraftLine(_vat_account(cfg, rate, chart, output=True), credit=amount,
                                         description=f"Hesaplanan KDV %{rate.normalize()}", vat_rate=rate,
                                         source_note="vat"))
        total = sum((l.credit for l in credits), Decimal(0))
        lines.append(DraftLine(recv_acc, debit=total - withholding, description=description,
                               source_note=f"receipt:{method}"))
        if withholding > 0:
            lines.append(DraftLine(cfg.settings.get("withholding_receivable_account", "136.01"), debit=withholding,
                                   description="Alıcı tarafından tevkif edilen KDV", source_note="withholding"))
        lines.extend(credits)
        expected = doc.total_amount

    # --- merge identical account/side/rate lines, drop zero lines ---------------------
    merged: OrderedDict[tuple, DraftLine] = OrderedDict()
    for l in lines:
        l.debit, l.credit = l.debit.quantize(CENT), l.credit.quantize(CENT)
        if l.debit == 0 and l.credit == 0:
            continue
        key = (l.account_code, l.debit > 0, l.vat_rate, l.source_note)
        if key in merged:
            merged[key].debit += l.debit
            merged[key].credit += l.credit
        else:
            merged[key] = l
    lines = list(merged.values())

    draft = JournalDraft(entry_date=doc.document_date, description=description,
                         document_number=doc.document_number, lines=lines, warnings=warnings)
    if draft.total_debit != draft.total_credit:
        raise EngineError("unbalanced", "engine produced an unbalanced entry")  # defensive; cannot happen
    payable_now = next((l.credit if direction == "purchase" else l.debit) for l in lines
                       if (l.source_note or "").startswith(("payment:", "receipt:")))
    if abs(payable_now - (expected if direction == "purchase" else expected - withholding)) > CENT:
        warn("total_mismatch", f"journal total {payable_now} differs from document total")
    if vat_total == 0 and doc.total_vat:
        warn("vat_not_posted", "document shows VAT but no VAT breakdown could be booked")
    if chart:
        for l in lines:
            if l.account_code not in chart:
                warn("account_missing", f"account {l.account_code} is not in the taxpayer's chart")
    return draft

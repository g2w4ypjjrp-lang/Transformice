"""UBL-TR parsing, extraction validation, Claude extractor (fake client), OCR,
storage and the deterministic accounting engine (VAT, withholding, KKEG, payments)."""

from datetime import date
from decimal import Decimal as D

import anthropic
import httpx
import pytest

from app.core.config import Settings
from app.services.accounting.engine import EngineError, Mapping, Rule, TaxpayerConfig, generate_journal
from app.services.extraction.claude import ClaudeExtractor, ExtractionError, parse_model_payload
from app.services.extraction.ocr import GoogleVisionOcr
from app.services.extraction.schema import ExtractedDocument
from app.services.extraction.ubl import UblError, parse_ubl
from app.services.extraction.validate import duplicate_fingerprint, validate_extraction
from app.services.files import FileRejected, validate_upload
from app.services.samples_docs import BUYER_VKN, SUPPLIER_VKN, invoice_pdf, thermal_receipt_image, ubl_invoice
from app.services.storage import LocalStorage, StorageError, SupabaseStorage
from tests.conftest import FakeAnthropic, receipt_payload

TODAY = date(2026, 11, 24)


# ============================================================================ UBL
class TestUbl:
    def test_purchase_invoice_multi_line(self):
        doc = parse_ubl(ubl_invoice(), taxpayer_tax_number=BUYER_VKN)
        assert doc.direction == "purchase" and doc.document_type == "e_invoice"
        assert doc.supplier.tax_number == SUPPLIER_VKN and doc.supplier.name.startswith("Örnek")
        assert (doc.total_net, doc.total_vat, doc.total_amount) == (D("2500.00"), D("500.00"), D("3000.00"))
        assert [(g.rate, g.base, g.amount) for g in doc.vat_breakdown] == [(D(20), D("2500.00"), D("500.00"))]
        assert len(doc.lines) == 2 and doc.lines[0].quantity == 10 and doc.payment_method == "bank"
        assert doc.ettn and doc.document_date == date(2026, 11, 3)

    def test_mixed_rates_earsiv(self):
        x = ubl_invoice(profile="EARSIVFATURA", lines=[("Ekmek", D(10), D("10.00"), D(1)),
                                                     ("Kitap", D(1), D("200.00"), D(10)),
                                                     ("Deterjan", D(2), D("50.00"), D(20))])
        doc = parse_ubl(x, taxpayer_tax_number=BUYER_VKN)
        assert doc.document_type == "e_archive_invoice"
        assert {g.rate: g.amount for g in doc.vat_breakdown} == {D(1): D("1.00"), D(10): D("20.00"), D(20): D("20.00")}

    def test_withholding(self):
        doc = parse_ubl(ubl_invoice(withholding=("624", D("0.7")),
                                    lines=[("Temizlik hizmeti", D(1), D("10000"), D(20))]), taxpayer_tax_number=BUYER_VKN)
        assert doc.withholding.code == "624" and doc.withholding.ratio == D("0.7")
        assert doc.withholding.amount == D("1400.00") and doc.total_amount == D("12000.00")

    def test_sales_direction(self):
        doc = parse_ubl(ubl_invoice(supplier_vkn=BUYER_VKN, buyer_vkn=SUPPLIER_VKN), taxpayer_tax_number=BUYER_VKN)
        assert doc.direction == "sales"

    def test_rejects_non_invoice_and_xxe(self):
        with pytest.raises(UblError):
            parse_ubl(b"<Order/>")
        evil = b"""<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><Invoice>&e;</Invoice>"""
        with pytest.raises(UblError):
            parse_ubl(evil)


# ============================================================================ validation
class TestValidation:
    def _doc(self, **kw):
        base = dict(document_type="thermal_receipt", document_date=date(2026, 11, 3), document_number="1",
                    supplier={"name": "X", "tax_number": SUPPLIER_VKN},
                    vat_breakdown=[{"rate": 20, "base": "1000.00", "amount": "200.00"}], total_amount="1200.00",
                    confidence={"total_amount": 0.99, "document_date": 0.99, "vat_breakdown": 0.99})
        base.update(kw)
        return ExtractedDocument.model_validate(base)

    def test_clean_document_high_confidence(self):
        _, issues, conf = validate_extraction(self._doc(), source="ocr_ai", today=TODAY)
        assert issues == [] and conf >= 0.95

    @pytest.mark.parametrize("kw,code", [
        ({"vat_breakdown": [{"rate": 20, "base": "1000.00", "amount": "150.00"}], "total_amount": "1150.00"},
         "vat_amount_mismatch"),
        ({"total_amount": "1300.00"}, "total_mismatch"),
        ({"vat_breakdown": [{"rate": 7, "base": "100", "amount": "7"}], "total_amount": "107"}, "vat_rate_invalid"),
        ({"vat_breakdown": [{"rate": 18, "base": "100", "amount": "18"}], "total_amount": "118"}, "vat_rate_outdated"),
        ({"document_date": date(2027, 1, 1)}, "date_in_future"),
        ({"document_date": None}, "date_missing"),
        ({"total_amount": None}, "total_missing"),
        ({"supplier": {"name": "X", "tax_number": "1234567891"}}, "supplier_tax_number_invalid"),
        ({"vat_breakdown": []}, "vat_unknown"),
    ])
    def test_issues(self, kw, code):
        _, issues, conf = validate_extraction(self._doc(**kw), source="ocr_ai", today=TODAY)
        assert code in {i["code"] for i in issues}
        assert conf < 0.95

    def test_breakdown_derived_from_lines(self):
        doc = self._doc(vat_breakdown=[], total_amount="330.00", lines=[
            {"description": "a", "net_amount": "100", "vat_rate": "10", "vat_amount": "10"},
            {"description": "b", "net_amount": "200", "vat_rate": "10", "vat_amount": "20"}])
        doc, issues, _ = validate_extraction(doc, source="ocr_ai", today=TODAY)
        assert [(g.rate, g.base, g.amount) for g in doc.vat_breakdown] == [(D(10), D(300), D(30))]
        assert "vat_breakdown_derived" in {i["code"] for i in issues}

    def test_fingerprint(self):
        assert duplicate_fingerprint(self._doc()) == f"{SUPPLIER_VKN}|1|2026-11-03|1200.00"
        assert duplicate_fingerprint(self._doc(document_date=None)) is None


# ============================================================================ Claude extractor
class TestClaude:
    def _settings(self):
        return Settings(anthropic_api_key="sk-test", environment="test")

    def test_extract_parses_structured_output_and_sends_schema(self):
        fake = FakeAnthropic(receipt_payload())
        res = ClaudeExtractor(self._settings(), client=fake).extract(thermal_receipt_image(), "image/jpeg",
                                                                      ocr_text="TOPLAM *1.200,00")
        assert res.document.total_amount == D("1200.00") and res.document.payment_method == "cash"
        call = fake.calls[0]
        assert call["model"] == "claude-opus-5-5"
        assert call["output_config"]["format"]["type"] == "json_schema"
        assert call["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in call["betas"]
        blocks = call["messages"][0]["content"]
        assert blocks[0]["type"] == "image" and "OCR" in blocks[1]["text"]

    def test_pdf_sent_as_document(self):
        fake = FakeAnthropic(receipt_payload())
        ClaudeExtractor(self._settings(), client=fake).extract(invoice_pdf(), "application/pdf")
        assert fake.calls[0]["messages"][0]["content"][0]["type"] == "document"

    def test_refusal_and_truncation(self):
        for stop, code in (("refusal", "ai_refused"), ("max_tokens", "ai_truncated")):
            with pytest.raises(ExtractionError) as exc:
                ClaudeExtractor(self._settings(), client=FakeAnthropic({}, stop_reason=stop)).extract(
                    thermal_receipt_image(), "image/jpeg")
            assert exc.value.code == code

    def test_connection_error_is_retryable_code(self):
        err = anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com"))
        with pytest.raises(ExtractionError) as exc:
            ClaudeExtractor(self._settings(), client=FakeAnthropic(raises=err)).extract(thermal_receipt_image(),
                                                                                         "image/jpeg")
        assert exc.value.code == "ai_unavailable"

    def test_missing_key(self):
        with pytest.raises(ExtractionError) as exc:
            ClaudeExtractor(Settings(environment="test", anthropic_api_key=None))
        assert exc.value.code == "ai_not_configured"

    def test_payload_parsing_never_guesses(self):
        doc = parse_model_payload(receipt_payload(total_amount="1.234,56", total_vat="abc", document_date="03.11.2026",
                                                  withholding={"code": None, "ratio": "7", "amount": None}))
        assert doc.total_amount == D("1234.56")
        assert doc.total_vat is None and doc.document_date is None and doc.withholding is None


class TestOcr:
    def test_google_vision_request(self):
        seen = {}

        def handler(req: httpx.Request):
            seen["url"] = str(req.url)
            return httpx.Response(200, json={"responses": [{"fullTextAnnotation": {"text": "TOPLAM 1.200,00"}}]})

        ocr = GoogleVisionOcr("key123", client=httpx.Client(transport=httpx.MockTransport(handler)))
        assert ocr.read_text(b"img", "image/jpeg") == "TOPLAM 1.200,00"
        assert "key=key123" in seen["url"]
        assert ocr.read_text(b"%PDF", "application/pdf") is None


# ============================================================================ files & storage
class TestFiles:
    def test_rejections(self):
        with pytest.raises(FileRejected) as e:
            validate_upload(b"MZ\x90\x00 executable", 10_000)
        assert e.value.code == "unsupported_type"
        with pytest.raises(FileRejected) as e:
            validate_upload(b"%PDF-" + b"0" * 200, 100)
        assert e.value.code == "file_too_large"
        with pytest.raises(FileRejected) as e:
            validate_upload(b"\xff\xd8\xffnot really a jpeg", 10_000)
        assert e.value.code == "corrupt_image"
        with pytest.raises(FileRejected) as e:
            validate_upload(b"<?xml version='1.0'?><Order/>", 10_000)
        assert e.value.code == "unsupported_xml"

    def test_image_metadata_stripped(self):
        import io

        from PIL import Image
        img = Image.new("RGB", (800, 600), "white")
        exif = Image.Exif()
        exif[0x8825] = {2: (41, 0, 0)}  # GPSInfo
        buf = io.BytesIO()
        img.save(buf, "JPEG", exif=exif)
        out = validate_upload(buf.getvalue(), 10_000_000)
        assert not Image.open(io.BytesIO(out.content)).getexif()


class TestStorage:
    def test_local_sign_verify_and_traversal(self, tmp_path):
        s = LocalStorage(Settings(environment="test", local_storage_dir=str(tmp_path)))
        s.put("documents", "f/a.jpg", b"x", "image/jpeg")
        token = s.sign("documents", "f/a.jpg")
        assert s.verify(token)["p"] == "f/a.jpg"
        with pytest.raises(StorageError):
            s.verify(token[:-2] + "xx")
        with pytest.raises(StorageError):
            s.verify(s.sign("documents", "f/a.jpg", ttl=-1))
        with pytest.raises(StorageError):
            s.put("documents", "../escape.txt", b"x", "text/plain")
        with pytest.raises(StorageError):
            s.put("documents", "f/a.jpg", b"y", "image/jpeg")  # no silent overwrite

    def test_supabase_rest_calls(self):
        calls = []

        def handler(req: httpx.Request):
            calls.append((req.method, req.url.path, req.headers.get("authorization")))
            if "/sign/" in req.url.path:
                return httpx.Response(200, json={"signedURL": "/object/sign/documents/f/a.jpg?token=abc"})
            return httpx.Response(200, content=b"data")

        st = SupabaseStorage(Settings(environment="test", supabase_url="https://x.supabase.co",
                                      supabase_service_role_key="srk"),
                             client=httpx.Client(transport=httpx.MockTransport(handler)))
        st.put("documents", "f/a.jpg", b"1", "image/jpeg")
        assert st.get("documents", "f/a.jpg") == b"data"
        url = st.signed_url("documents", "f/a.jpg", download_name="a.jpg")
        assert url.startswith("https://x.supabase.co/storage/v1/object/sign/documents/f/a.jpg?token=abc")
        assert all(c[2] == "Bearer srk" for c in calls)


# ============================================================================ accounting engine
CFG = TaxpayerConfig(tax_number=BUYER_VKN)


def _doc(**kw) -> ExtractedDocument:
    base = dict(document_type="thermal_receipt", document_date=date(2026, 11, 3), document_number="0042",
                supplier={"name": "Örnek Akaryakıt", "tax_number": SUPPLIER_VKN},
                vat_breakdown=[{"rate": 20, "base": "1000.00", "amount": "200.00"}], total_amount="1200.00",
                payment_method="cash")
    base.update(kw)
    return ExtractedDocument.model_validate(base)


def _lines(draft):
    return [(l.account_code, l.debit, l.credit) for l in draft.lines]


class TestEngine:
    def test_cash_receipt_vat20(self):
        d = generate_journal(_doc(supplier={"name": "Örnek Ticaret", "tax_number": SUPPLIER_VKN}), CFG)
        assert _lines(d) == [("770.01", D("1000.00"), 0), ("191.20", D("200.00"), 0), ("100.01", 0, D("1200.00"))]
        assert d.total_debit == d.total_credit and d.warnings == []

    @pytest.mark.parametrize("supplier,item,account", [
        ("Örnek Akaryakıt", None, "770.02"), ("ABC Ltd", "KURŞUNSUZ 95", "770.02"),
        ("Köşe Lokanta", None, "770.04"), ("XYZ A.Ş.", "A4 FOTOKOPİ KAĞIDI", "770.03"),
        ("Turkcell İletişim", None, "770.05"), ("Enerjisa", None, "770.07")])
    def test_expense_account_from_content(self, supplier, item, account):
        lines = [{"description": item, "total_amount": "1200.00", "vat_rate": "20"}] if item else []
        d = generate_journal(_doc(supplier={"name": supplier}, lines=lines), CFG)
        assert d.lines[0].account_code == account
        assert {w["code"] for w in d.warnings} == {"account_by_category"}

    def test_category_ignored_when_account_not_in_chart(self):
        d = generate_journal(_doc(), CFG, chart={"770.01", "191.20", "100.01"})
        assert d.lines[0].account_code == "770.01"

    def test_vat_line_labels(self):
        d = generate_journal(_doc(supplier={"name": "Örnek Ticaret"}), CFG)
        assert d.lines[1].description == "KDV %20"
        s = generate_journal(_doc(document_type="e_invoice", direction="sales", payment_method=None,
                                  buyer={"name": "Alıcı", "tax_number": SUPPLIER_VKN}), CFG)
        assert s.lines[-1].description == "Hesaplanan KDV %20"

    def test_multi_vat_rates(self):
        d = generate_journal(_doc(vat_breakdown=[{"rate": 1, "base": "100", "amount": "1"},
                                                 {"rate": 10, "base": "200", "amount": "20"},
                                                 {"rate": 20, "base": "300", "amount": "60"}], total_amount="681"), CFG)
        assert ("191.01", D("1.00"), 0) in _lines(d) and ("191.10", D("20.00"), 0) in _lines(d)
        assert ("100.01", 0, D("681.00")) in _lines(d) and d.total_debit == D("681.00")

    @pytest.mark.parametrize("method,account", [("credit_card", "300.01"), ("bank", "102.01"),
                                                ("supplier_credit", "320.01")])
    def test_payment_methods(self, method, account):
        d = generate_journal(_doc(payment_method=method), CFG)
        assert d.lines[-1].account_code == account and d.lines[-1].credit == D("1200.00")

    def test_invoice_without_payment_info_goes_to_supplier(self):
        d = generate_journal(_doc(document_type="e_invoice", payment_method=None), CFG)
        assert d.lines[-1].account_code == "320.01"

    def test_withholding_7_10(self):
        d = generate_journal(_doc(document_type="e_invoice", payment_method=None,
                                  vat_breakdown=[{"rate": 20, "base": "10000", "amount": "2000"}],
                                  total_amount="12000", withholding={"code": "624", "ratio": "0.7", "amount": "1400"}),
                             CFG)
        assert _lines(d) == [("770.02", D("10000.00"), 0), ("191.20", D("2000.00"), 0),
                             ("360.02", 0, D("1400.00")), ("320.01", 0, D("10600.00"))]

    def test_mapping_and_kkeg(self):
        m = Mapping("supplier_tax_number", SUPPLIER_VKN, "770.02")
        assert generate_journal(_doc(), CFG, mappings=[m]).lines[0].account_code == "770.02"
        kkeg = Mapping("supplier_name", "akaryakıt", "689.01", is_non_deductible=True)
        d = generate_journal(_doc(), CFG, mappings=[kkeg])
        assert _lines(d) == [("689.01", D("1200.00"), 0), ("100.01", 0, D("1200.00"))]
        assert d.warnings[0]["code"] == "non_deductible"

    def test_rule_overrides_mapping_and_payment(self):
        m = Mapping("supplier_tax_number", SUPPLIER_VKN, "770.02")
        r = Rule("büyük akaryakıt", {"all": [{"field": "total_amount", "op": "gte", "value": 1000}]},
                 {"expense_account": "760.01", "payment_method": "credit_card"})
        d = generate_journal(_doc(), CFG, mappings=[m], rules=[r])
        assert d.lines[0].account_code == "760.01" and d.lines[-1].account_code == "300.01"

    def test_sales_invoice(self):
        d = generate_journal(_doc(document_type="e_invoice", direction="sales", payment_method=None,
                                  buyer={"name": "Alıcı A.Ş.", "tax_number": SUPPLIER_VKN}), CFG)
        assert _lines(d) == [("120.01", D("1200.00"), 0), ("600.01", 0, D("1000.00")), ("391.20", 0, D("200.00"))]

    def test_unknown_vat_books_gross_with_warning(self):
        d = generate_journal(_doc(vat_breakdown=[], supplier={"name": "Örnek Ticaret"}), CFG)
        assert _lines(d) == [("770.01", D("1200.00"), 0), ("100.01", 0, D("1200.00"))]
        assert {w["code"] for w in d.warnings} == {"vat_unknown"}

    def test_total_mismatch_and_missing_account_warnings(self):
        d = generate_journal(_doc(total_amount="1250.00"), CFG, chart={"770.01", "100.01"})
        codes = {w["code"] for w in d.warnings}
        assert "total_mismatch" in codes and "account_missing" in codes

    def test_vat_account_fallback_and_custom_settings(self):
        d = generate_journal(_doc(), CFG, chart={"770.01", "100.01", "191.01"})
        assert d.lines[1].account_code == "191.01"  # 191.20 not in chart → taxpayer default
        cfg = TaxpayerConfig(tax_number=BUYER_VKN, settings={"input_vat_accounts": {"20": "191.02.20"}})
        assert generate_journal(_doc(), cfg).lines[1].account_code == "191.02.20"

    @pytest.mark.parametrize("kw,code", [({"document_date": None}, "date_missing"),
                                         ({"total_amount": None}, "total_missing"),
                                         ({"currency": "EUR"}, "currency_unsupported")])
    def test_refuses_incomplete(self, kw, code):
        with pytest.raises(EngineError) as e:
            generate_journal(_doc(**kw), CFG)
        assert e.value.code == code

    def test_always_balanced_on_ubl_inputs(self):
        for x in (ubl_invoice(), ubl_invoice(withholding=("624", D("0.5"))),
                  ubl_invoice(lines=[("a", D(3), D("33.33"), D(1)), ("b", D(7), D("12.47"), D(10)),
                                     ("c", D(1), D("999.99"), D(20))])):
            d = generate_journal(parse_ubl(x, taxpayer_tax_number=BUYER_VKN), CFG)
            assert d.total_debit == d.total_credit > 0

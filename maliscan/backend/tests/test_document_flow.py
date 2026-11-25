"""End-to-end: camera upload → worker (fake Claude) → review → correction →
journal edit → approval → Luca export → signed download; plus XML path,
duplicates, permissions, dashboard and super-admin endpoints."""

import csv
import io
import uuid
from decimal import Decimal as D

import anthropic
import httpx
import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.services.extraction.claude import ClaudeExtractor
from app.services.samples_docs import BUYER_VKN, thermal_receipt_image, ubl_invoice
from app.services.storage import get_storage
from app.worker.main import claim_job, run_job
from tests.conftest import FakeAnthropic, receipt_payload


@pytest.fixture
def office(client, make_user, auth_headers, add_member):
    owner, clerk, viewer = make_user(), make_user(), make_user()
    h = auth_headers(owner)
    firm = client.post("/api/v1/firms", headers=h, json={"name": "Akış SMMM"}).json()["id"]
    add_member(uuid.UUID(firm), clerk, "clerk")
    add_member(uuid.UUID(firm), viewer, "viewer")
    tp = client.post(f"/api/v1/firms/{firm}/taxpayers", headers=h,
                     json={"taxpayer_type": "company", "title": "Deneme Mükellef Ltd. Şti.",
                           "tax_number": BUYER_VKN}).json()
    return {"h": h, "firm": firm, "tp": tp["id"], "clerk": auth_headers(clerk), "viewer": auth_headers(viewer),
            "owner": owner}


def _upload(client, office, content, name="fis.jpg", headers=None, source="camera"):
    return client.post(f"/api/v1/firms/{office['firm']}/taxpayers/{office['tp']}/documents",
                       headers=headers or office["h"], files={"file": (name, content, "application/octet-stream")},
                       data={"source": source, "capture_metadata": '{"device":"test","lat":41.0,"auto":true}'})


def _work(engine, payload=None, firm=None, **fake_kw):
    """Run every queued job with a fake Claude client."""
    settings = get_settings()
    extractor = ClaudeExtractor(settings.model_copy(update={"anthropic_api_key": "sk-test"}),
                                client=FakeAnthropic(payload or receipt_payload(), **fake_kw))
    results = []
    while (job := claim_job(engine, "test-worker", firm)) is not None:
        results.append(run_job(job, engine=engine, storage=get_storage(), settings=settings, claude=extractor))
    return results


def test_full_receipt_flow(client, office, engine):
    r = _upload(client, office, thermal_receipt_image(tilt=3))
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["status"] == "queued" and doc["source"] == "camera"
    assert _work(engine, firm=office['firm']) == ["succeeded"]

    base = f"/api/v1/firms/{office['firm']}/documents/{doc['id']}"
    detail = client.get(base, headers=office["h"]).json()
    assert detail["document"]["status"] == "needs_review"
    assert detail["extraction"]["source"] == "ocr_ai" and detail["extraction"]["ai_model"] == "claude-opus-5-5"
    assert float(detail["extraction"]["overall_confidence"]) >= 0.85
    j = detail["journal"]
    assert j["status"] == "draft" and D(str(j["total_debit"])) == D("1200.00")
    assert [l["account_code"] for l in j["lines"]] == ["770.02", "191.20", "100.01"]  # fuel → 770.02 by content
    assert j["lines"][0]["account_name"] == "Akaryakıt ve Taşıt Giderleri"

    # GPS data from capture metadata was dropped; file served via signed URL
    meta = engine.connect().execute(text("SELECT capture_metadata FROM documents WHERE id = :d"),
                                    {"d": doc["id"]}).scalar()
    assert "lat" not in meta and meta["device"] == "test"
    img = client.get(detail["file_url"].replace("http://testserver", ""))
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"

    # clerk corrects the expense category through the extraction (draft rebuilt by worker job)
    data = detail["extraction"]["data"]
    data["payment_method"] = "credit_card"
    r = client.put(f"{base}/extraction", headers=office["clerk"], json=data)
    assert r.status_code == 200, r.text
    assert _work(engine, firm=office['firm']) == ["succeeded"]
    j = client.get(base, headers=office["h"]).json()["journal"]
    assert j["lines"][-1]["account_code"] == "300.01"

    # accountant edits the journal manually (fuel → 770.02)
    edit = {"entry_date": "2026-11-03", "description": "Akaryakıt", "lines": [
        {"account_code": "770.02", "debit": "1000.00"}, {"account_code": "191.20", "debit": "200.00"},
        {"account_code": "300.01", "credit": "1200.00"}]}
    assert client.put(f"{base}/journal", headers=office["h"],
                      json={**edit, "lines": edit["lines"][:2] + [{"account_code": "300.01", "credit": "1199"}]}
                      ).json()["error"]["code"] == "unbalanced"
    assert client.put(f"{base}/journal", headers=office["clerk"], json=edit).status_code == 403
    r = client.put(f"{base}/journal", headers=office["h"], json=edit)
    assert r.status_code == 200 and r.json()["lines"][0]["account_code"] == "770.02"

    # approval: viewer/clerk cannot, accountant/owner can
    assert client.post(f"{base}/approve", headers=office["clerk"], json={}).status_code == 403
    r = client.post(f"{base}/approve", headers=office["h"], json={"comment": "kontrol edildi"})
    assert r.status_code == 200, r.text
    detail = client.get(base, headers=office["h"]).json()
    assert detail["document"]["status"] == "approved" and detail["journal"]["status"] == "approved"
    assert [h["action"] for h in detail["history"]] == ["correct", "approve"]
    assert client.put(f"{base}/extraction", headers=office["h"], json=data).status_code == 409

    # learned mapping for the supplier
    maps = client.get(f"/api/v1/firms/{office['firm']}/taxpayers/{office['tp']}/mappings", headers=office["h"]).json()
    assert maps[0]["account_code"] == "770.02" and maps[0]["source"] == "learned"

    # Luca export
    r = client.post(f"/api/v1/firms/{office['firm']}/taxpayers/{office['tp']}/exports", headers=office["h"],
                    json={"all_approved": True})
    assert r.status_code == 200, r.text
    exp = r.json()
    assert exp["voucher_count"] == 1 and len(exp["files"]) == 1 and exp["verified_template"] is False
    assert "TEMPLATE_UNVERIFIED" in {i["code"] for i in exp["issues"]}
    f = client.get(exp["files"][0]["download_url"].replace("http://testserver", ""))
    assert f.status_code == 200 and "attachment" in f.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(f.content.decode("cp1254")), delimiter=";"))
    assert rows[0][:3] == ["Fiş No", "Fiş Tarihi", "Hesap Kodu"]
    assert [r[2] for r in rows[1:]] == ["770.02", "191.20", "300.01"]
    assert client.get(base, headers=office["h"]).json()["document"]["status"] == "exported"
    # nothing left; re-export refused; reopen refused
    assert client.post(f"/api/v1/firms/{office['firm']}/taxpayers/{office['tp']}/exports", headers=office["h"],
                       json={"all_approved": True}).json()["error"]["code"] == "nothing_to_export"
    assert client.post(f"{base}/reopen", headers=office["h"], json={}).status_code == 409

    dash = client.get(f"/api/v1/firms/{office['firm']}/dashboard", headers=office["viewer"]).json()
    assert dash["totals"]["exported"] == 1 and dash["taxpayers"][0]["documents"] == 1
    assert len(dash["daily"]) == 30


def test_xml_invoice_needs_no_ai_and_detects_duplicates(client, office, engine):
    xml = ubl_invoice(lines=[("Toner", D(1), D("500.00"), D(20))])
    assert _upload(client, office, xml, "fatura.xml", source="upload").status_code == 201
    # an AI client that would fail proves the XML path never calls it
    err = anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com"))
    assert _work(engine, firm=office['firm'], raises=err) == ["succeeded"]
    docs = client.get(f"/api/v1/firms/{office['firm']}/documents?status=needs_review", headers=office["h"]).json()
    assert docs["total"] == 1 and docs["items"][0]["document_type"] == "e_invoice"
    first = docs["items"][0]["id"]
    detail = client.get(f"/api/v1/firms/{office['firm']}/documents/{first}", headers=office["h"]).json()
    assert detail["extraction"]["source"] == "ubl_xml" and float(detail["extraction"]["overall_confidence"]) == 1.0
    assert detail["journal"]["lines"][-1]["account_code"] == "102.01"  # PaymentMeansCode 42 → bank

    # same file again → rejected immediately
    r = _upload(client, office, xml, "fatura.xml")
    assert r.status_code == 409 and r.json()["error"]["code"] == "duplicate_file"
    # same invoice content in a different file (whitespace) → flagged as possible duplicate
    assert _upload(client, office, xml.replace(b"<cbc:Note>", b"<cbc:Note> "), "kopya.xml").status_code == 201
    _work(engine, firm=office['firm'])
    dup = client.get(f"/api/v1/firms/{office['firm']}/documents?duplicates_only=true", headers=office["h"]).json()
    assert dup["total"] == 1 and dup["items"][0]["duplicate_of"] == first
    dup_url = f"/api/v1/firms/{office['firm']}/documents/{dup['items'][0]['id']}"
    r = client.post(f"{dup_url}/approve", headers=office["h"], json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "possible_duplicate"
    assert client.post(f"{dup_url}/reject", headers=office["h"], json={"comment": "mükerrer"}).status_code == 200
    assert client.post(f"{dup_url}/reopen", headers=office["h"], json={}).status_code == 200

    # bulk approval skips the duplicate, approves the original
    r = client.post(f"/api/v1/firms/{office['firm']}/documents/bulk-approve", headers=office["h"],
                    json={"document_ids": [first, dup["items"][0]["id"]]}).json()
    assert r["approved"] == [first] and r["skipped"][0]["reason"] == "possible_duplicate"


def test_upload_validation_and_permissions(client, office, engine):
    assert _upload(client, office, b"GIF89a....", "x.gif").json()["error"]["code"] == "unsupported_type"
    assert _upload(client, office, thermal_receipt_image(), headers=office["viewer"]).status_code == 403
    assert _upload(client, office, thermal_receipt_image(), headers=office["clerk"]).status_code == 201
    other_firm_user = office["h"]  # same user, but a random taxpayer id of no firm
    r = client.post(f"/api/v1/firms/{office['firm']}/taxpayers/{uuid.uuid4()}/documents", headers=other_firm_user,
                    files={"file": ("a.jpg", thermal_receipt_image(), "image/jpeg")})
    assert r.status_code == 404


def test_ai_failure_marks_document_failed_after_retries(client, office, engine):
    r = _upload(client, office, thermal_receipt_image(tilt=-2))
    doc_id = r.json()["id"]
    # refusal is not retryable → dead on first attempt
    assert _work(engine, firm=office['firm'], payload={}, stop_reason="refusal") == ["dead"]
    d = client.get(f"/api/v1/firms/{office['firm']}/documents/{doc_id}", headers=office["h"]).json()
    assert d["document"]["status"] == "failed" and d["document"]["error_code"] == "ai_refused"
    assert client.post(f"/api/v1/firms/{office['firm']}/documents/{doc_id}/reprocess",
                       headers=office["clerk"]).status_code == 200
    assert _work(engine, firm=office['firm']) == ["succeeded"]


def test_low_quality_extraction_has_issues_and_no_auto_approval(client, office, engine):
    payload = receipt_payload(total_amount="1250.00",
                              confidence={k: 0.5 for k in receipt_payload()["confidence"]})
    doc_id = _upload(client, office, thermal_receipt_image(tilt=1)).json()["id"]
    _work(engine, payload, firm=office['firm'])
    d = client.get(f"/api/v1/firms/{office['firm']}/documents/{doc_id}", headers=office["h"]).json()
    assert "total_mismatch" in {i["code"] for i in d["extraction"]["validation_issues"]}
    assert float(d["extraction"]["overall_confidence"]) < 0.5
    r = client.post(f"/api/v1/firms/{office['firm']}/documents/bulk-approve", headers=office["h"],
                    json={"document_ids": [doc_id]}).json()
    assert r["approved"] == [] and r["skipped"][0]["reason"] in ("validation_errors", "low_confidence")


def test_tenant_isolation_over_http(client, office, engine, make_user, auth_headers):
    doc_id = _upload(client, office, thermal_receipt_image(tilt=4)).json()["id"]
    stranger = auth_headers(make_user())
    client.post("/api/v1/firms", headers=stranger, json={"name": "Başka Ofis"})
    for method, path in (("get", f"/documents/{doc_id}"), ("get", "/documents"), ("get", "/dashboard"),
                         ("post", f"/documents/{doc_id}/approve"), ("get", "/exports")):
        r = getattr(client, method)(f"/api/v1/firms/{office['firm']}{path}", headers=stranger,
                                    **({"json": {}} if method == "post" else {}))
        assert r.status_code == 404, path


def test_super_admin_endpoints(client, office, make_user, auth_headers, engine):
    admin = auth_headers(make_user(super_admin=True))
    assert client.get("/api/v1/admin/overview", headers=office["h"]).status_code == 403
    ov = client.get("/api/v1/admin/overview", headers=admin).json()
    assert ov["firms"] >= 1 and "jobs" in ov
    firms = client.get("/api/v1/admin/firms?q=Akış", headers=admin).json()
    assert any(f["id"] == office["firm"] for f in firms)
    r = client.put("/api/v1/admin/plans/pilot_test", headers=admin, json={
        "code": "pilot_test", "name": "Pilot", "price_monthly": "0", "max_users": 5, "max_taxpayers": 20,
        "max_documents_per_month": 500})
    assert r.status_code == 200
    r = client.put(f"/api/v1/admin/firms/{office['firm']}/subscription", headers=admin,
                   json={"plan_code": "pilot_test"})
    assert r.status_code == 200
    usage = client.get(f"/api/v1/firms/{office['firm']}/usage", headers=office["h"]).json()
    assert usage["limits"]["taxpayers"] == 20
    assert client.patch(f"/api/v1/admin/firms/{office['firm']}", headers=admin,
                        json={"status": "suspended"}).status_code == 200
    assert client.get(f"/api/v1/firms/{office['firm']}", headers=office["h"]).status_code == 404
    client.patch(f"/api/v1/admin/firms/{office['firm']}", headers=admin, json={"status": "active"})
    assert client.put("/api/v1/admin/config/uploads.max_file_bytes", headers=admin,
                      json={"value": 10485760}).status_code == 200
    assert client.get("/api/v1/admin/errors", headers=admin).status_code == 200
    assert isinstance(client.get("/api/v1/admin/jobs", headers=admin).json(), list)
    logs = client.get("/api/v1/admin/audit-logs", headers=admin).json()
    assert {"admin.firm_status", "admin.plan_saved"} <= {l["action"] for l in logs}


def test_free_mode_reads_receipt_with_local_ocr(client, office, engine):
    """No ANTHROPIC_API_KEY: on-server Tesseract + Turkish receipt rules read the photo,
    and the engine codes the accounts — no typing needed."""
    import shutil

    if not shutil.which("tesseract"):
        pytest.skip("tesseract not installed")
    doc_id = _upload(client, office, thermal_receipt_image(tilt=1)).json()["id"]
    settings = get_settings()
    assert settings.anthropic_api_key is None
    while (job := claim_job(engine, "free-worker", office["firm"])) is not None:
        assert run_job(job, engine=engine, storage=get_storage(), settings=settings) == "succeeded"
    d = client.get(f"/api/v1/firms/{office['firm']}/documents/{doc_id}", headers=office["h"]).json()
    ex = d["extraction"]
    assert ex["source"] == "ocr_rules" and ex["validation_issues"][0]["code"] == "read_by_free_ocr"
    assert ex["data"]["total_amount"] == "1200.00" and ex["data"]["document_date"] == "2026-11-03"
    assert ex["data"]["payment_method"] == "cash" and ex["data"]["document_number"] == "0042"
    assert float(ex["overall_confidence"]) < 0.85  # always reviewed individually
    j = d["journal"]
    assert [(l["account_code"], str(l["debit"]), str(l["credit"])) for l in j["lines"]] == [
        ("770.02", "1000.00", "0.00"), ("191.20", "200.00", "0.00"), ("100.01", "0.00", "1200.00")]


def test_free_mode_falls_back_to_manual_entry(client, office, engine):
    """OCR disabled/unavailable: the document waits for manual entry and the corrected
    data produces a balanced draft journal."""
    doc_id = _upload(client, office, thermal_receipt_image(tilt=5)).json()["id"]
    settings = get_settings().model_copy(update={"local_ocr_enabled": False})
    while (job := claim_job(engine, "free-worker", office["firm"])) is not None:
        assert run_job(job, engine=engine, storage=get_storage(), settings=settings) == "succeeded"
    base = f"/api/v1/firms/{office['firm']}/documents/{doc_id}"
    d = client.get(base, headers=office["h"]).json()
    assert d["document"]["status"] == "needs_review"
    assert d["extraction"]["validation_issues"][0]["code"] == "manual_entry_required"
    assert d["journal"] is None
    data = d["extraction"]["data"]
    data.update(document_date="2026-11-03", total_amount="1200.00", payment_method="cash",
                supplier={"name": "Örnek Ticaret", "tax_number": None, "tax_office": None, "address": None},
                vat_breakdown=[{"rate": "20", "base": "1000.00", "amount": "200.00"}])
    r = client.put(f"{base}/extraction", headers=office["h"], json=data)
    assert r.status_code == 200, r.text
    j = r.json()["journal"]
    assert j and str(j["total_debit"]) in ("1200.00", "1200")


def test_document_deleted_while_queued_is_skipped_quietly(client, office, engine):
    doc_id = _upload(client, office, thermal_receipt_image(tilt=3)).json()["id"]
    job = claim_job(engine, "w", office["firm"])
    # user deletes while the job is running — must not wait for OCR nor be flipped to "failed"
    assert client.delete(f"/api/v1/firms/{office['firm']}/documents/{doc_id}", headers=office["h"]).status_code == 204
    assert run_job(job, engine=engine, storage=get_storage(), settings=get_settings()) == "skipped"
    with engine.connect() as c:
        st, deleted = c.execute(text("SELECT status, deleted_at IS NOT NULL FROM documents WHERE id = :d"),
                                {"d": doc_id}).one()
        job_status = c.execute(text("SELECT status, last_error_code FROM processing_jobs WHERE id = :j"),
                               {"j": job["id"]}).one()
    assert deleted and st != "failed"
    assert tuple(job_status) == ("succeeded", "skipped_document_gone")

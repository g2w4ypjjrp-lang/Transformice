"""Free single-instance hosting mode: embedded worker + startup migrations + bucket bootstrap."""

import time
import uuid

import httpx
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.services.samples_docs import BUYER_VKN, ubl_invoice
from app.services.storage import SupabaseStorage


def test_embedded_worker_processes_jobs(make_user, auth_headers, monkeypatch):
    monkeypatch.setenv("EMBEDDED_WORKER", "true")
    monkeypatch.setenv("RUN_MIGRATIONS_ON_STARTUP", "true")
    monkeypatch.setenv("WORKER_POLL_SECONDS", "0.2")
    get_settings.cache_clear()
    try:
        from app.main import create_app

        with TestClient(create_app()) as c:
            h = auth_headers(make_user())
            firm = c.post("/api/v1/firms", headers=h, json={"name": "Ücretsiz Ofis"}).json()["id"]
            tp = c.post(f"/api/v1/firms/{firm}/taxpayers", headers=h,
                        json={"taxpayer_type": "company", "title": "Free Ltd", "tax_number": BUYER_VKN}).json()["id"]
            doc = c.post(f"/api/v1/firms/{firm}/taxpayers/{tp}/documents", headers=h,
                         files={"file": ("f.xml", ubl_invoice(uuid=str(uuid.uuid4())), "application/xml")}).json()
            for _ in range(50):
                status = c.get(f"/api/v1/firms/{firm}/documents/{doc['id']}", headers=h).json()["document"]["status"]
                if status == "needs_review":
                    break
                time.sleep(0.2)
            assert status == "needs_review"
    finally:
        get_settings.cache_clear()


def test_supabase_bucket_bootstrap_creates_missing_private_buckets():
    created = []

    def handler(req: httpx.Request):
        if req.method == "GET":
            return httpx.Response(200, json=[{"id": "documents", "name": "documents", "public": False}])
        created.append(req.read())
        return httpx.Response(200, json={"name": "exports"})

    st = SupabaseStorage(Settings(environment="test", supabase_url="https://x.supabase.co",
                                  supabase_service_role_key="srk"),
                         client=httpx.Client(transport=httpx.MockTransport(handler)))
    st.ensure_buckets(["documents", "exports"])
    assert len(created) == 1 and b'"public":false' in created[0].replace(b" ", b"")

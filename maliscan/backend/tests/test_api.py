"""HTTP-level tests: authentication, profile, firm endpoints, error format."""

import uuid

import pytest

from tests.conftest import make_token


class TestAuthentication:
    def test_health_is_public(self, client):
        assert client.get("/api/v1/health").json() == {"status": "ok"}

    def test_readiness_reports_schema_version(self, client):
        r = client.get("/api/v1/health/ready")
        assert r.status_code == 200
        assert r.json()["schema_version"] >= "0005"

    def test_missing_token(self, client):
        r = client.get("/api/v1/me")
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "unauthenticated"

    @pytest.mark.parametrize("kwargs", [
        {"secret": "wrong-secret-wrong-secret-wrong-secret"},
        {"exp_offset": -3600},
        {"aud": "anon"},
        {"role": "service_role"},
    ])
    def test_invalid_tokens_rejected(self, client, make_user, kwargs):
        uid = make_user()
        r = client.get("/api/v1/me", headers={"Authorization": f"Bearer {make_token(uid, **kwargs)}"})
        assert r.status_code == 401

    def test_garbage_token(self, client):
        assert client.get("/api/v1/me", headers={"Authorization": "Bearer abc.def"}).status_code == 401

    def test_unsigned_token_rejected(self, client, make_user):
        import jwt

        uid = make_user()
        tok = jwt.encode({"sub": str(uid), "aud": "authenticated", "exp": 9999999999}, key=None, algorithm="none")
        assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 401

    def test_valid_token_without_profile(self, client, auth_headers):
        r = client.get("/api/v1/me", headers=auth_headers(uuid.uuid4()))
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "profile_missing"

    def test_disabled_user(self, client, make_user, owner_conn, auth_headers):
        uid = make_user()
        owner_conn.execute("UPDATE public.users SET is_active = false WHERE id = %s", (uid,))
        r = client.get("/api/v1/me", headers=auth_headers(uid))
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "user_disabled"


class TestMe:
    def test_profile_and_update(self, client, make_user, auth_headers):
        uid = make_user(full_name="Mehmet Öz")
        h = auth_headers(uid)
        r = client.get("/api/v1/me", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert body["full_name"] == "Mehmet Öz" and body["memberships"] == [] and body["is_super_admin"] is False

        r = client.patch("/api/v1/me", headers=h, json={"full_name": "Mehmet Öztürk", "locale": "en"})
        assert r.status_code == 200
        assert r.json()["full_name"] == "Mehmet Öztürk" and r.json()["locale"] == "en"

    def test_cannot_escalate_via_profile_update(self, client, make_user, auth_headers):
        uid = make_user()
        r = client.patch("/api/v1/me", headers=auth_headers(uid), json={"is_super_admin": True})
        assert r.status_code == 422


class TestFirms:
    def test_create_list_get(self, client, make_user, auth_headers):
        uid = make_user()
        h = auth_headers(uid)
        r = client.post("/api/v1/firms", headers=h, json={"name": "Yıldız SMMM Bürosu", "tax_number": "1234567890"})
        assert r.status_code == 201, r.text
        firm = r.json()
        assert firm["my_role"] == "owner"
        assert firm["subscription"]["plan_code"] == "trial"
        assert firm["subscription"]["status"] == "trialing"

        listed = client.get("/api/v1/firms", headers=h).json()
        assert [f["id"] for f in listed] == [firm["id"]]
        assert client.get(f"/api/v1/firms/{firm['id']}", headers=h).status_code == 200

        me = client.get("/api/v1/me", headers=h).json()
        assert me["memberships"] == [{"firm_id": firm["id"], "firm_name": "Yıldız SMMM Bürosu", "role": "owner"}]

    def test_invalid_tax_number(self, client, make_user, auth_headers):
        r = client.post("/api/v1/firms", headers=auth_headers(make_user()), json={"name": "Ofis", "tax_number": "12AB"})
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "validation_error"

    def test_non_member_gets_404(self, client, make_user, make_firm, auth_headers):
        firm = make_firm(make_user())
        outsider = auth_headers(make_user())
        for path in ("", "/members", "/audit-logs"):
            r = client.get(f"/api/v1/firms/{firm}{path}", headers=outsider)
            assert r.status_code == 404, path
        r = client.patch(f"/api/v1/firms/{firm}", headers=outsider, json={"name": "Hacked"})
        assert r.status_code == 404

    def test_role_restrictions(self, client, make_user, make_firm, add_member, auth_headers):
        owner, viewer = make_user(), make_user()
        firm = make_firm(owner)
        add_member(firm, viewer, "viewer")
        vh = auth_headers(viewer)
        assert client.get(f"/api/v1/firms/{firm}", headers=vh).status_code == 200
        assert len(client.get(f"/api/v1/firms/{firm}/members", headers=vh).json()) == 2
        assert client.patch(f"/api/v1/firms/{firm}", headers=vh, json={"name": "Yeni"}).status_code == 403
        assert client.get(f"/api/v1/firms/{firm}/audit-logs", headers=vh).status_code == 403

    def test_update_writes_audit_log(self, client, make_user, make_firm, auth_headers):
        owner = make_user()
        firm = make_firm(owner)
        h = auth_headers(owner)
        r = client.patch(f"/api/v1/firms/{firm}", headers=h, json={"name": "Güncel Ofis", "tax_office": "Kadıköy"})
        assert r.status_code == 200 and r.json()["name"] == "Güncel Ofis"
        logs = client.get(f"/api/v1/firms/{firm}/audit-logs", headers=h).json()
        assert [entry["action"] for entry in logs] == ["firm.updated", "firm.created"]
        assert logs[0]["metadata"] == {"fields": ["name", "tax_office"]}
        assert logs[0]["actor_user_id"] == str(owner)

    def test_invalid_uuid_path(self, client, make_user, auth_headers):
        assert client.get("/api/v1/firms/not-a-uuid", headers=auth_headers(make_user())).status_code == 422

    def test_security_headers_and_request_id(self, client):
        r = client.get("/api/v1/health", headers={"X-Request-ID": "abc123"})
        assert r.headers["X-Request-ID"] == "abc123"
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert r.headers["Cache-Control"] == "no-store"

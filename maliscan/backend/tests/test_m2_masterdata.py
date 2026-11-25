"""M2: members & invitations, taxpayers, chart of accounts, mappings, rules, plan limits."""

import uuid

import pytest

from app.domain.rules import Conditions, matches
from app.domain.tax_ids import is_valid_tckn, is_valid_vkn, make_valid_tckn
from tests.conftest import next_vkn


@pytest.fixture
def office(client, make_user, auth_headers):
    owner = make_user()
    h = auth_headers(owner)
    firm = client.post("/api/v1/firms", headers=h, json={"name": "Test SMMM"}).json()["id"]
    return {"owner": owner, "h": h, "firm": firm}


def _tp(client, office, **kw):
    body = {"taxpayer_type": "company", "title": "Deneme Ltd. Şti.", "tax_number": next_vkn(), **kw}
    return client.post(f"/api/v1/firms/{office['firm']}/taxpayers", headers=office["h"], json=body)


class TestTaxIds:
    def test_known_values(self):
        assert is_valid_tckn("10000000146")
        assert not is_valid_tckn("10000000145")
        assert not is_valid_tckn("01234567890")
        assert is_valid_vkn("1234567890")
        assert not is_valid_vkn("1234567891")
        assert is_valid_tckn(make_valid_tckn("123456789"))


class TestTaxpayers:
    def test_create_seeds_chart_and_lists(self, client, office):
        r = _tp(client, office)
        assert r.status_code == 201, r.text
        tp = r.json()
        assert tp["default_cash_account"] == "100.01"
        accounts = client.get(f"/api/v1/firms/{office['firm']}/taxpayers/{tp['id']}/accounts?postable_only=true",
                              headers=office["h"]).json()
        codes = {a["code"] for a in accounts}
        assert {"100.01", "191.20", "320.01", "770.01"} <= codes and "770" not in codes
        listed = client.get(f"/api/v1/firms/{office['firm']}/taxpayers", headers=office["h"]).json()
        assert listed[0]["stats"] == {"documents": 0, "pending_review": 0}

    def test_individual_requires_valid_tckn(self, client, office):
        r = _tp(client, office, taxpayer_type="individual", tax_number="10000000145")
        assert r.status_code == 422
        r = _tp(client, office, taxpayer_type="individual", tax_number="10000000146")
        assert r.status_code == 201

    def test_vkn_checksum_is_a_warning(self, client, office):
        r = _tp(client, office, tax_number="1234567891")
        assert r.status_code == 201 and r.json()["warnings"] == ["vkn_checksum_mismatch"]

    def test_duplicate_tax_number_conflict(self, client, office):
        vkn = next_vkn()
        assert _tp(client, office, tax_number=vkn).status_code == 201
        r = _tp(client, office, tax_number=vkn)
        assert r.status_code == 409

    def test_update_and_archive(self, client, office):
        tp = _tp(client, office).json()
        url = f"/api/v1/firms/{office['firm']}/taxpayers/{tp['id']}"
        r = client.patch(url, headers=office["h"], json={"default_payment_method": "bank", "locked_until": "2026-09-30",
                                                         "default_expense_account": "770.99"})
        assert r.status_code == 200 and r.json()["default_payment_method"] == "bank"
        assert client.patch(url, headers=office["h"], json={"default_expense_account": "77X"}).status_code == 422
        assert client.delete(url, headers=office["h"]).status_code == 204
        assert client.get(url, headers=office["h"]).status_code == 404

    def test_clerk_cannot_create_taxpayer(self, client, office, make_user, add_member, auth_headers):
        clerk = make_user()
        add_member(uuid.UUID(office["firm"]), clerk, "clerk")
        r = client.post(f"/api/v1/firms/{office['firm']}/taxpayers", headers=auth_headers(clerk),
                        json={"taxpayer_type": "company", "title": "X Ltd", "tax_number": next_vkn()})
        assert r.status_code == 403


class TestChart:
    def test_add_sub_account_creates_parents_and_unposts_parent(self, client, office):
        tp = _tp(client, office, seed_default_chart=False).json()
        base = f"/api/v1/firms/{office['firm']}/taxpayers/{tp['id']}/accounts"
        assert client.post(base, headers=office["h"], json={"code": "770.01", "name": "Genel"}).status_code == 201
        r = client.post(base, headers=office["h"], json={"code": "770.01.003", "name": "Akaryakıt"})
        assert r.status_code == 201 and r.json()["is_postable"] is True and r.json()["account_class"] == "cost"
        acc = {a["code"]: a for a in client.get(base, headers=office["h"]).json()}
        assert acc["770"]["is_postable"] is False and acc["770.01"]["is_postable"] is False

    def test_save_chart_download_and_copy_to_all(self, client, office):
        src = _tp(client, office, seed_default_chart=False).json()
        other = _tp(client, office, seed_default_chart=False).json()
        base = f"/api/v1/firms/{office['firm']}/taxpayers"
        client.post(f"{base}/{src['id']}/accounts", headers=office["h"], json={"code": "770.01.003", "name": "Akaryakıt"})
        dl = client.get(f"{base}/{src['id']}/accounts/export", headers=office["h"])
        assert dl.status_code == 200 and "hesap_plani.csv" in dl.headers["content-disposition"]
        lines = dl.content.decode("cp1254").split("\r\n")
        assert lines[0] == "Hesap Kodu;Hesap Adı" and "770.01.003;Akaryakıt" in lines
        r = client.post(f"{base}/{src['id']}/accounts/copy-to-all", headers=office["h"])
        assert r.status_code == 200 and r.json() == {"taxpayers": 1, "accounts": 3}
        acc = {a["code"]: a for a in client.get(f"{base}/{other['id']}/accounts", headers=office["h"]).json()}
        assert acc["770.01.003"]["is_postable"] and not acc["770.01"]["is_postable"] and not acc["770"]["is_postable"]

    def test_import_luca_style_csv(self, client, office):
        tp = _tp(client, office, seed_default_chart=False).json()
        csv_bytes = "Hesap Kodu;Hesap Adı\r\n100;KASA\r\n100.01;MERKEZ KASA\r\n320.01.001;ABC GIDA LTD\r\nXX;bad\r\n" \
            .encode("cp1254")
        r = client.post(f"/api/v1/firms/{office['firm']}/taxpayers/{tp['id']}/accounts/import", headers=office["h"],
                        files={"file": ("plan.csv", csv_bytes, "text/csv")})
        assert r.status_code == 200, r.text
        assert r.json()["accepted"] == 3 and len(r.json()["rejected"]) == 1
        codes = {a["code"]: a for a in client.get(f"/api/v1/firms/{office['firm']}/taxpayers/{tp['id']}/accounts",
                                                   headers=office["h"]).json()}
        assert codes["100"]["is_postable"] is False and codes["100.01"]["is_postable"] is True
        assert "320.01" in codes and codes["320.01.001"]["name"] == "ABC GIDA LTD"


class TestMappingsAndRules:
    def test_mapping_crud(self, client, office):
        tp = _tp(client, office).json()
        base = f"/api/v1/firms/{office['firm']}/taxpayers/{tp['id']}/mappings"
        m = client.post(base, headers=office["h"], json={"match_field": "supplier_tax_number",
                                                         "match_value": "1234567890", "account_code": "770.02"})
        assert m.status_code == 201
        dup = client.post(base, headers=office["h"], json={"match_field": "supplier_tax_number",
                                                           "match_value": "1234567890", "account_code": "770.03"})
        assert dup.status_code == 409
        assert len(client.get(base, headers=office["h"]).json()) == 1
        assert client.delete(f"{base}/{m.json()['id']}", headers=office["h"]).status_code == 204

    def test_rule_validation_and_crud(self, client, office):
        tp = _tp(client, office).json()
        base = f"/api/v1/firms/{office['firm']}/taxpayers/{tp['id']}/rules"
        bad = client.post(base, headers=office["h"], json={"name": "x1", "conditions": {"all": []},
                                                           "actions": {"expense_account": "770.02"}})
        assert bad.status_code == 422
        bad2 = client.post(base, headers=office["h"], json={
            "name": "Shell", "conditions": {"all": [{"field": "supplier_name", "op": "contains", "value": "shell"}]},
            "actions": {"expense_account": "77"}})
        assert bad2.status_code == 422
        ok = client.post(base, headers=office["h"], json={
            "name": "Shell → akaryakıt", "conditions": {"all": [{"field": "supplier_name", "op": "contains",
                                                                  "value": "shell"}]},
            "actions": {"expense_account": "770.02", "payment_method": "cash"}})
        assert ok.status_code == 201, ok.text
        assert client.get(base, headers=office["h"]).json()[0]["actions"]["expense_account"] == "770.02"

    def test_rule_matching_is_turkish_case_insensitive(self):
        c = Conditions.model_validate({"all": [{"field": "supplier_name", "op": "contains", "value": "İSTASYON"},
                                               {"field": "total_amount", "op": "gt", "value": 100}]})
        assert matches(c, {"supplier_name": "Shell Petrol istasyonu", "total_amount": "150.00"})
        assert not matches(c, {"supplier_name": "Shell Petrol istasyonu", "total_amount": "99.99"})
        assert not matches(c, {"supplier_name": "Opet", "total_amount": "150.00"})
        any_c = Conditions.model_validate({"any": [{"field": "item_description", "op": "contains", "value": "ekmek"}]})
        assert matches(any_c, {"item_description": ["Süt", "EKMEK 250G"]})


class TestMembersAndInvitations:
    def test_invite_accept_flow(self, client, office, make_user, auth_headers):
        email = f"yeni-{uuid.uuid4().hex[:6]}@example.com"
        r = client.post(f"/api/v1/firms/{office['firm']}/invitations", headers=office["h"],
                        json={"email": email, "role": "accountant"})
        assert r.status_code == 201
        token = r.json()["token"]
        assert r.json()["accept_url"].endswith(token)

        stranger = make_user()
        assert client.post("/api/v1/invitations/accept", headers=auth_headers(stranger),
                           json={"token": token}).status_code == 403
        invitee = make_user(email)
        r = client.post("/api/v1/invitations/accept", headers=auth_headers(invitee), json={"token": token})
        assert r.status_code == 200 and r.json()["firm_id"] == office["firm"]
        me = client.get("/api/v1/me", headers=auth_headers(invitee)).json()
        assert me["memberships"][0]["role"] == "accountant"
        # single use
        assert client.post("/api/v1/invitations/accept", headers=auth_headers(invitee),
                           json={"token": token}).status_code == 404

    def test_revoked_invitation_cannot_be_used(self, client, office, make_user, auth_headers):
        email = f"x-{uuid.uuid4().hex[:6]}@example.com"
        inv = client.post(f"/api/v1/firms/{office['firm']}/invitations", headers=office["h"],
                          json={"email": email, "role": "viewer"}).json()
        assert client.delete(f"/api/v1/firms/{office['firm']}/invitations/{inv['id']}",
                             headers=office["h"]).status_code == 204
        assert client.post("/api/v1/invitations/accept", headers=auth_headers(make_user(email)),
                           json={"token": inv["token"]}).status_code == 404

    def test_role_change_and_removal(self, client, office, make_user, add_member, auth_headers):
        admin, clerk = make_user(), make_user()
        add_member(uuid.UUID(office["firm"]), admin, "admin")
        add_member(uuid.UUID(office["firm"]), clerk, "clerk")
        ah = auth_headers(admin)
        url = f"/api/v1/firms/{office['firm']}/members"
        assert client.patch(f"{url}/{clerk}", headers=ah, json={"role": "accountant"}).status_code == 200
        assert client.patch(f"{url}/{clerk}", headers=ah, json={"role": "owner"}).status_code == 403
        assert client.patch(f"{url}/{office['owner']}", headers=ah, json={"status": "disabled"}).status_code == 404
        assert client.delete(f"{url}/{clerk}", headers=ah).status_code == 204
        assert client.delete(f"{url}/{office['owner']}", headers=office["h"]).status_code == 422  # last owner


class TestPlanLimits:
    def test_taxpayer_limit(self, client, office, owner_conn):
        owner_conn.execute("""INSERT INTO public.subscription_plans (code, name, max_taxpayers, max_users,
                                max_documents_per_month) VALUES ('tiny_test', 'Tiny', 1, 2, 1)
                              ON CONFLICT (code) DO NOTHING""")
        owner_conn.execute("""UPDATE public.subscriptions SET plan_id = (SELECT id FROM public.subscription_plans
                              WHERE code = 'tiny_test') WHERE firm_id = %s""", (office["firm"],))
        assert _tp(client, office).status_code == 201
        r = _tp(client, office)
        assert r.status_code == 402 and r.json()["error"]["code"] == "plan_limit_exceeded"
        usage = client.get(f"/api/v1/firms/{office['firm']}/usage", headers=office["h"]).json()
        assert usage["usage"]["taxpayers"] == 1 and usage["limits"]["taxpayers"] == 1

    def test_expired_trial_blocks_new_taxpayers(self, client, office, owner_conn):
        owner_conn.execute("""UPDATE public.subscriptions SET current_period_start = now() - interval '30 days',
                              current_period_end = now() - interval '1 day' WHERE firm_id = %s""", (office["firm"],))
        r = _tp(client, office)
        assert r.status_code == 402 and r.json()["error"]["code"] == "subscription_inactive"

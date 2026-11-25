"""Row Level Security / tenant isolation tests at the database level.

These run queries as the `authenticated` role with JWT claims — the same path
every API request takes — and verify one office can never read or write
another office's data.
"""

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.conftest import next_vkn


def _insert_taxpayer(conn, firm_id):
    return conn.execute(
        text("""INSERT INTO public.taxpayers (firm_id, taxpayer_type, title, tax_number)
                VALUES (:f, 'company', 'Gizli Ltd. Şti.', :t) RETURNING id"""),
        {"f": firm_id, "t": next_vkn()},
    ).scalar_one()


@pytest.fixture
def two_firms(make_user, make_firm):
    a_user, b_user = make_user(), make_user()
    return {"a_user": a_user, "a_firm": make_firm(a_user), "b_user": b_user, "b_firm": make_firm(b_user)}


def test_firm_bootstrap_creates_owner_and_trial(two_firms, as_user):
    with as_user(two_firms["a_user"]) as conn:
        role = conn.execute(text("SELECT app.firm_role(:f)"), {"f": two_firms["a_firm"]}).scalar_one()
        sub = conn.execute(
            text("""SELECT p.code, s.status FROM public.subscriptions s
                    JOIN public.subscription_plans p ON p.id = s.plan_id WHERE s.firm_id = :f"""),
            {"f": two_firms["a_firm"]},
        ).one()
    assert role == "owner"
    assert tuple(sub) == ("trial", "trialing")


def test_member_cannot_see_other_firm(two_firms, as_user):
    with as_user(two_firms["a_user"]) as conn:
        tp = _insert_taxpayer(conn, two_firms["a_firm"])
    with as_user(two_firms["b_user"]) as conn:
        assert conn.execute(text("SELECT count(*) FROM public.firms WHERE id = :f"),
                            {"f": two_firms["a_firm"]}).scalar_one() == 0
        assert conn.execute(text("SELECT count(*) FROM public.taxpayers WHERE id = :t"), {"t": tp}).scalar_one() == 0
        assert conn.execute(text("SELECT count(*) FROM public.firm_members WHERE firm_id = :f"),
                            {"f": two_firms["a_firm"]}).scalar_one() == 0
        assert conn.execute(text("SELECT count(*) FROM public.subscriptions WHERE firm_id = :f"),
                            {"f": two_firms["a_firm"]}).scalar_one() == 0
        # Users of other firms are invisible as well.
        assert conn.execute(text("SELECT count(*) FROM public.users WHERE id = :u"),
                            {"u": two_firms["a_user"]}).scalar_one() == 0


def test_member_cannot_write_into_other_firm(two_firms, as_user):
    with pytest.raises(DBAPIError) as exc:
        with as_user(two_firms["b_user"]) as conn:
            _insert_taxpayer(conn, two_firms["a_firm"])
    assert isinstance(exc.value.orig, psycopg.errors.InsufficientPrivilege)


def test_update_of_other_firm_rows_affects_nothing(two_firms, as_user):
    with as_user(two_firms["a_user"]) as conn:
        tp = _insert_taxpayer(conn, two_firms["a_firm"])
    with as_user(two_firms["b_user"]) as conn:
        res = conn.execute(text("UPDATE public.taxpayers SET title = 'hacked' WHERE id = :t"), {"t": tp})
        assert res.rowcount == 0
        res = conn.execute(text("DELETE FROM public.taxpayers WHERE id = :t"), {"t": tp})
        assert res.rowcount == 0
        res = conn.execute(text("UPDATE public.firms SET name = 'hacked' WHERE id = :f"), {"f": two_firms["a_firm"]})
        assert res.rowcount == 0


def test_cross_tenant_reference_blocked_by_composite_fk(two_firms, as_user):
    """Even with a valid own firm_id, a child row cannot reference another firm's parent."""
    with as_user(two_firms["a_user"]) as conn:
        foreign_tp = _insert_taxpayer(conn, two_firms["a_firm"])
    with pytest.raises(DBAPIError) as exc:
        with as_user(two_firms["b_user"]) as conn:
            conn.execute(
                text("""INSERT INTO public.chart_of_accounts
                        (firm_id, taxpayer_id, code, name, account_class, normal_balance)
                        VALUES (:f, :t, '770.01', 'Genel Yönetim Giderleri', 'expense', 'debit')"""),
                {"f": two_firms["b_firm"], "t": foreign_tp},
            )
    assert isinstance(exc.value.orig, psycopg.errors.ForeignKeyViolation)


def test_unauthenticated_sees_nothing(two_firms, as_user):
    with as_user(None) as conn:
        assert conn.execute(text("SELECT count(*) FROM public.firms")).scalar_one() == 0
        assert conn.execute(text("SELECT count(*) FROM public.taxpayers")).scalar_one() == 0
        assert conn.execute(text("SELECT count(*) FROM public.users")).scalar_one() == 0


def test_create_firm_requires_authentication(as_user):
    with pytest.raises(DBAPIError) as exc:
        with as_user(None) as conn:
            conn.execute(text("SELECT app.create_firm('X Ofis', NULL)"))
    assert isinstance(exc.value.orig, psycopg.errors.InsufficientPrivilege)


def test_direct_firm_insert_is_not_allowed(make_user, as_user):
    uid = make_user()
    with pytest.raises(DBAPIError):
        with as_user(uid) as conn:
            conn.execute(text("INSERT INTO public.firms (name) VALUES ('Bypass Ofis')"))


@pytest.mark.parametrize("role,can_write_master,can_upload", [
    ("admin", True, True),
    ("accountant", True, True),
    ("clerk", False, True),
    ("viewer", False, False),
])
def test_role_permissions(role, can_write_master, can_upload, make_user, make_firm, add_member,
                          make_taxpayer, as_user):
    owner, member = make_user(), make_user()
    firm = make_firm(owner)
    add_member(firm, member, role)
    tp = make_taxpayer(firm)

    def try_master():
        with as_user(member) as conn:
            _insert_taxpayer(conn, firm)

    def try_upload():
        with as_user(member) as conn:
            conn.execute(
                text("""INSERT INTO public.documents (firm_id, taxpayer_id, uploaded_by, source, file_kind,
                          storage_path, mime_type, file_size, sha256)
                        VALUES (:f, :t, auth.uid(), 'camera', 'image', :p, 'image/jpeg', 1234, :h)"""),
                {"f": firm, "t": tp, "p": f"{firm}/x/{member}.jpg", "h": "a" * 64},
            )

    for action, allowed in ((try_master, can_write_master), (try_upload, can_upload)):
        if allowed:
            action()
        else:
            with pytest.raises(DBAPIError):
                action()

    # Everyone in the firm can read.
    with as_user(member) as conn:
        assert conn.execute(text("SELECT count(*) FROM public.taxpayers WHERE id = :t"), {"t": tp}).scalar_one() == 1


def test_document_uploaded_by_must_be_caller(make_user, make_firm, make_taxpayer, as_user):
    owner, other = make_user(), make_user()
    firm = make_firm(owner)
    tp = make_taxpayer(firm)
    with pytest.raises(DBAPIError):
        with as_user(owner) as conn:
            conn.execute(
                text("""INSERT INTO public.documents (firm_id, taxpayer_id, uploaded_by, source, file_kind,
                          storage_path, mime_type, file_size, sha256)
                        VALUES (:f, :t, :u, 'upload', 'pdf', :p, 'application/pdf', 10, :h)"""),
                {"f": firm, "t": tp, "u": other, "p": f"{firm}/a.pdf", "h": "b" * 64},
            )


def test_storage_path_must_be_namespaced_by_firm(make_user, make_firm, make_taxpayer, as_user):
    owner = make_user()
    firm = make_firm(owner)
    tp = make_taxpayer(firm)
    with pytest.raises(DBAPIError) as exc:
        with as_user(owner) as conn:
            conn.execute(
                text("""INSERT INTO public.documents (firm_id, taxpayer_id, uploaded_by, source, file_kind,
                          storage_path, mime_type, file_size, sha256)
                        VALUES (:f, :t, auth.uid(), 'upload', 'pdf', 'other-firm/a.pdf', 'application/pdf', 10, :h)"""),
                {"f": firm, "t": tp, "h": "c" * 64},
            )
    assert isinstance(exc.value.orig, psycopg.errors.CheckViolation)


def test_admin_cannot_grant_owner_but_owner_can(make_user, make_firm, add_member, as_user):
    owner, admin, newbie = make_user(), make_user(), make_user()
    firm = make_firm(owner)
    add_member(firm, admin, "admin")
    with pytest.raises(DBAPIError):
        with as_user(admin) as conn:
            conn.execute(text("INSERT INTO public.firm_members (firm_id, user_id, role) VALUES (:f, :u, 'owner')"),
                         {"f": firm, "u": newbie})
    with as_user(owner) as conn:
        conn.execute(text("INSERT INTO public.firm_members (firm_id, user_id, role) VALUES (:f, :u, 'owner')"),
                     {"f": firm, "u": newbie})


def test_admin_cannot_demote_owner(make_user, make_firm, add_member, as_user):
    owner, admin = make_user(), make_user()
    firm = make_firm(owner)
    add_member(firm, admin, "admin")
    with as_user(admin) as conn:
        res = conn.execute(text("UPDATE public.firm_members SET role = 'viewer' WHERE firm_id = :f AND user_id = :u"),
                           {"f": firm, "u": owner})
        assert res.rowcount == 0


def test_last_owner_cannot_be_removed(make_user, make_firm, as_user):
    owner = make_user()
    firm = make_firm(owner)
    with pytest.raises(DBAPIError) as exc:
        with as_user(owner) as conn:
            conn.execute(text("DELETE FROM public.firm_members WHERE firm_id = :f AND user_id = :u"),
                         {"f": firm, "u": owner})
    assert isinstance(exc.value.orig, psycopg.errors.CheckViolation)


def test_disabled_member_loses_access(make_user, make_firm, add_member, owner_conn, as_user):
    owner, member = make_user(), make_user()
    firm = make_firm(owner)
    add_member(firm, member, "accountant")
    owner_conn.execute("UPDATE public.firm_members SET status = 'disabled' WHERE user_id = %s", (member,))
    with as_user(member) as conn:
        assert conn.execute(text("SELECT count(*) FROM public.firms WHERE id = :f"), {"f": firm}).scalar_one() == 0


def test_suspended_firm_is_inaccessible(make_user, make_firm, owner_conn, as_user):
    owner = make_user()
    firm = make_firm(owner)
    owner_conn.execute("UPDATE public.firms SET status = 'suspended' WHERE id = %s", (firm,))
    with as_user(owner) as conn:
        assert conn.execute(text("SELECT app.firm_role(:f)"), {"f": firm}).scalar_one() is None


def test_user_cannot_make_self_super_admin(make_user, as_user):
    uid = make_user()
    with pytest.raises(DBAPIError) as exc:
        with as_user(uid) as conn:
            conn.execute(text("UPDATE public.users SET is_super_admin = true WHERE id = auth.uid()"))
    assert isinstance(exc.value.orig, psycopg.errors.InsufficientPrivilege)


def test_super_admin_can_read_all_firms(two_firms, make_user, as_user):
    admin = make_user(super_admin=True)
    with as_user(admin) as conn:
        n = conn.execute(text("SELECT count(*) FROM public.firms WHERE id IN (:a, :b)"),
                         {"a": two_firms["a_firm"], "b": two_firms["b_firm"]}).scalar_one()
    assert n == 2


def test_audit_log_is_append_only_and_scoped(two_firms, as_user, owner_conn):
    with as_user(two_firms["a_user"]) as conn:
        assert conn.execute(text("SELECT count(*) FROM public.audit_logs WHERE firm_id = :f"),
                            {"f": two_firms["a_firm"]}).scalar_one() >= 1
    with as_user(two_firms["b_user"]) as conn:
        assert conn.execute(text("SELECT count(*) FROM public.audit_logs WHERE firm_id = :f"),
                            {"f": two_firms["a_firm"]}).scalar_one() == 0
        # Cannot forge an entry into another firm's log.
    with pytest.raises(DBAPIError):
        with as_user(two_firms["b_user"]) as conn:
            conn.execute(text("SELECT app.write_audit_log(:f, 'firm.hacked', 'firm', :f)"),
                         {"f": two_firms["a_firm"]})
    # Not even the superuser can rewrite history.
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        owner_conn.execute("UPDATE public.audit_logs SET action = 'x.y' WHERE firm_id = %s", (two_firms["a_firm"],))


def test_platform_config_hidden_from_regular_users(make_user, as_user):
    uid = make_user()
    with as_user(uid) as conn:
        assert conn.execute(text("SELECT count(*) FROM public.platform_config")).scalar_one() == 0


def test_firm_and_user_can_be_deleted_but_audit_stays_immutable(make_user, make_firm, make_taxpayer, owner_conn):
    """Office termination / KVKK erasure must work despite the append-only audit log."""
    uid = make_user()
    firm = make_firm(uid)
    make_taxpayer(firm)
    before = owner_conn.execute("SELECT count(*) FROM audit_logs WHERE firm_id = %s", (firm,)).fetchone()[0]
    assert before >= 1
    owner_conn.execute("DELETE FROM public.firms WHERE id = %s", (firm,))
    owner_conn.execute("DELETE FROM auth.users WHERE id = %s", (uid,))
    assert owner_conn.execute("SELECT count(*) FROM public.users WHERE id = %s", (uid,)).fetchone()[0] == 0
    # audit rows survive, detached from the deleted firm/user
    assert owner_conn.execute("SELECT count(*) FROM audit_logs WHERE firm_id IS NULL AND action = 'firm.created' "
                              "AND actor_user_id IS NULL").fetchone()[0] >= 1
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        owner_conn.execute("DELETE FROM audit_logs WHERE action = 'firm.created'")

import shutil

import psycopg
import pytest

from app.db.migrate import database_dir, discover, run

EXPECTED_TABLES = {
    "firms", "users", "firm_members", "firm_invitations", "taxpayers", "chart_of_accounts",
    "account_mappings", "accounting_rules", "documents", "document_items", "extracted_data",
    "journal_entries", "journal_lines", "approvals", "export_jobs", "export_files",
    "subscriptions", "subscription_plans", "audit_logs", "processing_jobs", "platform_config",
}


def test_all_spec_tables_exist_with_rls(owner_conn):
    rows = owner_conn.execute(
        """SELECT c.relname, c.relrowsecurity FROM pg_class c
           JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = 'public' AND c.relkind = 'r'"""
    ).fetchall()
    tables = dict(rows)
    assert EXPECTED_TABLES <= set(tables), EXPECTED_TABLES - set(tables)
    assert all(tables.values()), [t for t, rls in tables.items() if not rls]


def test_tenant_tables_have_firm_id(owner_conn):
    tenant_tables = EXPECTED_TABLES - {"users", "subscription_plans", "platform_config", "firms"}
    cols = {
        r[0]
        for r in owner_conn.execute(
            """SELECT table_name FROM information_schema.columns
               WHERE table_schema = 'public' AND column_name = 'firm_id'"""
        )
    }
    assert tenant_tables <= cols, tenant_tables - cols


def test_rerun_is_noop(database):
    assert run(database, with_shim=True) == []


def test_modified_migration_is_rejected(database, tmp_path):
    copy = tmp_path / "database"
    shutil.copytree(database_dir(), copy)
    first = sorted((copy / "migrations").glob("*.sql"))[0]
    first.write_text(first.read_text() + "\n-- tampered\n")
    with pytest.raises(RuntimeError, match="modified after being applied"):
        run(database, directory=copy)


def test_migration_files_are_well_formed():
    files = discover(database_dir() / "migrations")
    assert [v for v, _, _ in files] == sorted(v for v, _, _ in files)
    assert len(files) >= 5


def test_anon_role_has_no_table_privileges(owner_conn):
    count = owner_conn.execute(
        """SELECT count(*) FROM information_schema.role_table_grants
           WHERE grantee = 'anon' AND table_schema = 'public'"""
    ).fetchone()[0]
    assert count == 0


def test_auth_user_trigger_creates_profile(owner_conn):
    uid = owner_conn.execute(
        "INSERT INTO auth.users (email, raw_user_meta_data) VALUES ('Trigger@Example.test', %s) RETURNING id",
        (psycopg.types.json.Jsonb({"full_name": "Ayşe Yılmaz"}),),
    ).fetchone()[0]
    row = owner_conn.execute("SELECT email, full_name FROM public.users WHERE id = %s", (uid,)).fetchone()
    assert row == ("Trigger@Example.test", "Ayşe Yılmaz")

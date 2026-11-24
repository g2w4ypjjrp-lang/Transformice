"""End-to-end export of approved journal entries through the DB (RLS-scoped)."""

import csv
import io
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.services.luca import LucaExportService
from app.services.luca.repository import ExportPermissionError, export_journal_entries


class MemoryStorage:
    bucket = "exports"

    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put(self, path, content, content_type):
        self.objects[path] = content


@pytest.fixture
def ctx(make_user, make_firm, make_taxpayer, add_member, owner_conn):
    owner, accountant, clerk = make_user(), make_user(), make_user()
    firm = make_firm(owner)
    add_member(firm, accountant, "accountant")
    add_member(firm, clerk, "clerk")
    tp = make_taxpayer(firm)
    for code, cls_, nb in (("770.01", "expense", "debit"), ("191.20", "asset", "debit"),
                           ("100.01", "asset", "debit")):
        owner_conn.execute(
            """INSERT INTO public.chart_of_accounts (firm_id, taxpayer_id, code, name, account_class, normal_balance)
               VALUES (%s, %s, %s, %s, %s, %s)""", (firm, tp, code, code, cls_, nb))
    return {"firm": firm, "tp": tp, "owner": owner, "accountant": accountant, "clerk": clerk}


def _journal(as_user, ctx, n, *, approve=True, net="100.00", vat="20.00", total="120.00", doc=None):
    with as_user(ctx["accountant"]) as conn:
        e = conn.execute(
            text("""INSERT INTO public.journal_entries (firm_id, taxpayer_id, entry_date, document_number, description)
                    VALUES (:f, :t, :d, :doc, 'Akaryakıt') RETURNING id"""),
            {"f": ctx["firm"], "t": ctx["tp"], "d": date(2026, 11, 1) + timedelta(days=n % 28),
             "doc": doc or f"F-{n}-{uuid.uuid4().hex[:4]}"},
        ).scalar_one()
        for no, (code, d, c) in enumerate((("770.01", net, "0"), ("191.20", vat, "0"), ("100.01", "0", total)), 1):
            conn.execute(
                text("""INSERT INTO public.journal_lines (firm_id, journal_entry_id, line_no, account_code, debit, credit)
                        VALUES (:f, :e, :n, :c, CAST(:d AS numeric), CAST(:cr AS numeric))"""),
                {"f": ctx["firm"], "e": e, "n": no, "c": code, "d": d, "cr": c})
        if approve:
            conn.execute(text("UPDATE public.journal_entries SET status = 'approved' WHERE id = :e"), {"e": e})
    return e


def _export(as_user, ctx, ids, user="accountant", **kw):
    storage = MemoryStorage()
    with as_user(ctx[user]) as conn:
        job, res = export_journal_entries(conn, firm_id=ctx["firm"], taxpayer_id=ctx["tp"], entry_ids=ids,
                                          service=LucaExportService(), storage=storage, **kw)
    return job, res, storage


def test_export_marks_entries_and_records_job(as_user, ctx):
    ids = [_journal(as_user, ctx, i) for i in range(53)]
    job, res, storage = _export(as_user, ctx, ids)
    assert job is not None and res.ok
    assert [f.voucher_count for f in res.files] == [50, 3]
    assert len(storage.objects) == 2
    assert all(p.startswith(f"{ctx['firm']}/{ctx['tp']}/{job}/") for p in storage.objects)
    with as_user(ctx["accountant"]) as conn:
        statuses = conn.execute(text("SELECT status, count(*) FROM public.journal_entries WHERE id = ANY(:i) GROUP BY 1"),
                                {"i": ids}).all()
        files = conn.execute(text("SELECT file_index, voucher_count FROM public.export_files WHERE export_job_id = :j "
                                  "ORDER BY 1"), {"j": job}).all()
        jobrow = conn.execute(text("SELECT status, voucher_count, file_count, template_version FROM public.export_jobs "
                                   "WHERE id = :j"), {"j": job}).one()
        per_file = conn.execute(text("SELECT count(DISTINCT export_file_id) FROM public.journal_entries "
                                     "WHERE id = ANY(:i)"), {"i": ids}).scalar_one()
    assert statuses == [("exported", 53)]
    assert [tuple(f) for f in files] == [(1, 50), (2, 3)]
    assert tuple(jobrow) == ("completed", 53, 2, "luca-fis-aktarim-2020-evidence")
    assert per_file == 2
    rows = list(csv.reader(io.StringIO(next(iter(storage.objects.values())).decode("cp1254")), delimiter=";"))
    assert rows[0][0] == "Fiş No" and len(rows) == 1 + 50 * 3


def test_reexport_is_refused(as_user, ctx):
    e = _journal(as_user, ctx, 1)
    job, res, _ = _export(as_user, ctx, [e])
    assert job
    job2, res2, storage = _export(as_user, ctx, [e])
    assert job2 is None and storage.objects == {}
    assert {i.code for i in res2.errors} == {"ALREADY_EXPORTED"}


def test_duplicate_receipt_against_history(as_user, ctx):
    first = _journal(as_user, ctx, 5, doc="AYNI-FIS")
    _export(as_user, ctx, [first])
    twin = _journal(as_user, ctx, 5, doc="AYNI-FIS")
    job, res, _ = _export(as_user, ctx, [twin])
    assert job is None and "DUPLICATE_OF_EXPORTED" in {i.code for i in res.errors}
    job, res, _ = _export(as_user, ctx, [twin], allowed_duplicate_ids=[twin])
    assert job is not None


def test_draft_and_unknown_entries_refused_and_nothing_written(as_user, ctx):
    ok = _journal(as_user, ctx, 1)
    draft = _journal(as_user, ctx, 2, approve=False)
    job, res, storage = _export(as_user, ctx, [ok, draft, uuid.uuid4()])
    assert job is None and storage.objects == {}
    assert {i.code for i in res.errors} == {"NOT_APPROVED", "NOT_FOUND"}
    with as_user(ctx["accountant"]) as conn:
        assert conn.execute(text("SELECT status FROM public.journal_entries WHERE id = :e"), {"e": ok}).scalar_one() \
            == "approved"


def test_account_missing_from_chart_blocks_export(as_user, ctx, owner_conn):
    e = _journal(as_user, ctx, 3)
    owner_conn.execute("UPDATE public.chart_of_accounts SET is_active = false WHERE taxpayer_id = %s AND code = '191.20'",
                       (ctx["tp"],))
    job, res, _ = _export(as_user, ctx, [e])
    assert job is None and "ACCOUNT_NOT_IN_CHART" in {i.code for i in res.errors}


def test_other_firm_cannot_export(as_user, ctx, make_user, make_firm):
    e = _journal(as_user, ctx, 4)
    outsider = make_user()
    make_firm(outsider)
    ctx2 = dict(ctx, outsider=outsider)
    with pytest.raises(ExportPermissionError):  # not a member of ctx's firm
        _export(as_user, ctx2, [e], user="outsider")


def test_clerk_cannot_export(as_user, ctx):
    e = _journal(as_user, ctx, 6)
    with pytest.raises(ExportPermissionError):
        _export(as_user, ctx, [e], user="clerk")
    with as_user(ctx["accountant"]) as conn:
        assert conn.execute(text("SELECT status FROM public.journal_entries WHERE id = :e"), {"e": e}).scalar_one() \
            == "approved"

"""Database-enforced accounting invariants for journal entries."""

from datetime import date

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError


@pytest.fixture
def ctx(make_user, make_firm, make_taxpayer, add_member):
    owner, accountant, clerk = make_user(), make_user(), make_user()
    firm = make_firm(owner)
    add_member(firm, accountant, "accountant")
    add_member(firm, clerk, "clerk")
    return {"firm": firm, "tp": make_taxpayer(firm), "owner": owner, "accountant": accountant, "clerk": clerk}


def _entry(conn, ctx, entry_date=date(2026, 11, 3)):
    return conn.execute(
        text("""INSERT INTO public.journal_entries (firm_id, taxpayer_id, entry_date, description)
                VALUES (:f, :t, :d, 'Akaryakıt fişi') RETURNING id"""),
        {"f": ctx["firm"], "t": ctx["tp"], "d": entry_date},
    ).scalar_one()


def _line(conn, ctx, entry, no, code, debit="0", credit="0"):
    conn.execute(
        text("""INSERT INTO public.journal_lines (firm_id, journal_entry_id, line_no, account_code, debit, credit)
                VALUES (:f, :e, :n, :c, CAST(:d AS numeric), CAST(:cr AS numeric))"""),
        {"f": ctx["firm"], "e": entry, "n": no, "c": code, "d": debit, "cr": credit},
    )


def _balanced_entry(as_user, ctx, **kw):
    with as_user(ctx["accountant"]) as conn:
        e = _entry(conn, ctx, **kw)
        _line(conn, ctx, e, 1, "770.01", debit="1000.00")
        _line(conn, ctx, e, 2, "191.01", debit="200.00")
        _line(conn, ctx, e, 3, "100.01", credit="1200.00")
    return e


def _approve(as_user, uid, entry):
    with as_user(uid) as conn:
        conn.execute(text("UPDATE public.journal_entries SET status = 'approved' WHERE id = :e"), {"e": entry})


def test_totals_are_maintained_by_trigger(as_user, ctx):
    e = _balanced_entry(as_user, ctx)
    with as_user(ctx["accountant"]) as conn:
        row = conn.execute(text("SELECT total_debit, total_credit, line_count FROM public.journal_entries WHERE id = :e"),
                           {"e": e}).one()
    assert (str(row[0]), str(row[1]), row[2]) == ("1200.00", "1200.00", 3)


def test_balanced_entry_can_be_approved_and_records_approver(as_user, ctx):
    e = _balanced_entry(as_user, ctx)
    _approve(as_user, ctx["accountant"], e)
    with as_user(ctx["accountant"]) as conn:
        row = conn.execute(text("SELECT status, approved_by, approved_at FROM public.journal_entries WHERE id = :e"),
                           {"e": e}).one()
    assert row[0] == "approved" and row[1] == ctx["accountant"] and row[2] is not None


def test_unbalanced_entry_cannot_be_approved(as_user, ctx):
    with as_user(ctx["accountant"]) as conn:
        e = _entry(conn, ctx)
        _line(conn, ctx, e, 1, "770.01", debit="1000.00")
        _line(conn, ctx, e, 2, "100.01", credit="999.99")
    with pytest.raises(DBAPIError) as exc:
        _approve(as_user, ctx["accountant"], e)
    assert isinstance(exc.value.orig, psycopg.errors.CheckViolation)
    assert "not balanced" in str(exc.value.orig)


def test_empty_entry_cannot_be_approved(as_user, ctx):
    with as_user(ctx["accountant"]) as conn:
        e = _entry(conn, ctx)
    with pytest.raises(DBAPIError):
        _approve(as_user, ctx["accountant"], e)


@pytest.mark.parametrize("debit,credit", [("0", "0"), ("10", "10"), ("-5", "0")])
def test_line_must_be_single_sided_and_positive(as_user, ctx, debit, credit):
    with pytest.raises(DBAPIError) as exc:
        with as_user(ctx["accountant"]) as conn:
            e = _entry(conn, ctx)
            _line(conn, ctx, e, 1, "770.01", debit=debit, credit=credit)
    assert isinstance(exc.value.orig, psycopg.errors.CheckViolation)


def test_invalid_account_code_rejected(as_user, ctx):
    with pytest.raises(DBAPIError) as exc:
        with as_user(ctx["accountant"]) as conn:
            e = _entry(conn, ctx)
            _line(conn, ctx, e, 1, "77A", debit="1")
    assert isinstance(exc.value.orig, psycopg.errors.CheckViolation)


def test_lines_are_read_only_after_approval(as_user, ctx):
    e = _balanced_entry(as_user, ctx)
    _approve(as_user, ctx["accountant"], e)
    with pytest.raises(DBAPIError) as exc:
        with as_user(ctx["accountant"]) as conn:
            conn.execute(text("UPDATE public.journal_lines SET debit = 1100 WHERE journal_entry_id = :e AND line_no = 1"),
                         {"e": e})
    assert "read-only" in str(exc.value.orig)
    with pytest.raises(DBAPIError):
        with as_user(ctx["accountant"]) as conn:
            _line(conn, ctx, e, 9, "689.01", debit="1")


def test_header_is_read_only_after_approval(as_user, ctx):
    e = _balanced_entry(as_user, ctx)
    _approve(as_user, ctx["accountant"], e)
    with pytest.raises(DBAPIError):
        with as_user(ctx["accountant"]) as conn:
            conn.execute(text("UPDATE public.journal_entries SET entry_date = '2026-01-01' WHERE id = :e"), {"e": e})


def test_clerk_cannot_approve(as_user, ctx):
    e = _balanced_entry(as_user, ctx)
    with as_user(ctx["clerk"]) as conn:
        res = conn.execute(text("UPDATE public.journal_entries SET status = 'approved' WHERE id = :e"), {"e": e})
        assert res.rowcount == 0  # RLS hides the row from the UPDATE
    with as_user(ctx["accountant"]) as conn:
        assert conn.execute(text("SELECT status FROM public.journal_entries WHERE id = :e"), {"e": e}).scalar_one() == "draft"


def test_entry_cannot_be_created_as_approved(as_user, ctx):
    with pytest.raises(DBAPIError):
        with as_user(ctx["accountant"]) as conn:
            conn.execute(
                text("""INSERT INTO public.journal_entries (firm_id, taxpayer_id, entry_date, status, approved_by, approved_at)
                        VALUES (:f, :t, '2026-11-01', 'approved', auth.uid(), now())"""),
                {"f": ctx["firm"], "t": ctx["tp"]},
            )


def test_locked_period_blocks_approval(as_user, ctx, owner_conn):
    owner_conn.execute("UPDATE public.taxpayers SET locked_until = '2026-10-31' WHERE id = %s", (ctx["tp"],))
    try:
        e = _balanced_entry(as_user, ctx, entry_date=date(2026, 10, 15))
        with pytest.raises(DBAPIError) as exc:
            _approve(as_user, ctx["accountant"], e)
        assert "locked" in str(exc.value.orig)
        ok = _balanced_entry(as_user, ctx, entry_date=date(2026, 11, 1))
        _approve(as_user, ctx["accountant"], ok)
    finally:
        owner_conn.execute("UPDATE public.taxpayers SET locked_until = NULL WHERE id = %s", (ctx["tp"],))


def test_invalid_status_transitions(as_user, ctx):
    e = _balanced_entry(as_user, ctx)
    # draft -> exported is not allowed (must be approved and have an export file)
    with pytest.raises(DBAPIError):
        with as_user(ctx["accountant"]) as conn:
            conn.execute(text("UPDATE public.journal_entries SET status = 'exported' WHERE id = :e"), {"e": e})
    # reject, then reopen to draft is allowed
    with as_user(ctx["accountant"]) as conn:
        conn.execute(text("UPDATE public.journal_entries SET status = 'rejected' WHERE id = :e"), {"e": e})
        conn.execute(text("UPDATE public.journal_entries SET status = 'draft' WHERE id = :e"), {"e": e})


def test_reopening_approved_entry_clears_approver(as_user, ctx):
    e = _balanced_entry(as_user, ctx)
    _approve(as_user, ctx["accountant"], e)
    with as_user(ctx["owner"]) as conn:
        conn.execute(text("UPDATE public.journal_entries SET status = 'draft' WHERE id = :e"), {"e": e})
        row = conn.execute(text("SELECT approved_by, approved_at FROM public.journal_entries WHERE id = :e"), {"e": e}).one()
    assert tuple(row) == (None, None)


def test_approved_entry_cannot_be_deleted(as_user, ctx):
    e = _balanced_entry(as_user, ctx)
    _approve(as_user, ctx["accountant"], e)
    with as_user(ctx["owner"]) as conn:
        assert conn.execute(text("DELETE FROM public.journal_entries WHERE id = :e"), {"e": e}).rowcount == 0


def test_approval_history_is_append_only(as_user, ctx):
    e = _balanced_entry(as_user, ctx)
    with as_user(ctx["accountant"]) as conn:
        conn.execute(text("""INSERT INTO public.approvals (firm_id, journal_entry_id, action, actor_user_id)
                             VALUES (:f, :e, 'approve', auth.uid())"""), {"f": ctx["firm"], "e": e})
    # clerk may log a correction but not an approval
    with pytest.raises(DBAPIError):
        with as_user(ctx["clerk"]) as conn:
            conn.execute(text("""INSERT INTO public.approvals (firm_id, journal_entry_id, action, actor_user_id)
                                 VALUES (:f, :e, 'approve', auth.uid())"""), {"f": ctx["firm"], "e": e})
    with pytest.raises(DBAPIError) as exc:
        with as_user(ctx["owner"]) as conn:
            conn.execute(text("UPDATE public.approvals SET comment = 'x' WHERE journal_entry_id = :e"), {"e": e})
    assert isinstance(exc.value.orig, psycopg.errors.InsufficientPrivilege)

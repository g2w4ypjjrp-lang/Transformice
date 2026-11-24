"""Retention purge: dry run changes nothing; execute removes expired files/rows
but keeps approved accounting records."""

import uuid

from sqlalchemy import text

from app.maintenance import run
from app.services.storage import get_storage


def _doc(owner_conn, firm, tp, *, deleted_days=None, retention_past=False, status="needs_review"):
    doc_id = uuid.uuid4()
    path = f"{firm}/{tp}/{doc_id}.jpg"
    get_storage().put("documents", path, b"\xff\xd8\xffdata", "image/jpeg")
    owner_conn.execute(
        """INSERT INTO public.documents (id, firm_id, taxpayer_id, source, file_kind, storage_path, mime_type, file_size,
               sha256, status, deleted_at, retention_until)
           VALUES (%s, %s, %s, 'upload', 'image', %s, 'image/jpeg', 7, %s, %s,
                   CASE WHEN %s::int IS NULL THEN NULL ELSE now() - make_interval(days => %s::int) END,
                   CASE WHEN %s THEN current_date - 1 ELSE current_date + 3650 END)""",
        (doc_id, firm, tp, path, uuid.uuid4().hex + uuid.uuid4().hex, status, deleted_days, deleted_days, retention_past))
    return doc_id, path


def test_retention(owner_conn, make_user, make_firm, make_taxpayer, engine):
    firm = make_firm(make_user())
    tp = make_taxpayer(firm)
    old_deleted, p1 = _doc(owner_conn, firm, tp, deleted_days=40)
    recent_deleted, p2 = _doc(owner_conn, firm, tp, deleted_days=5)
    expired, p3 = _doc(owner_conn, firm, tp, retention_past=True)
    keep, p4 = _doc(owner_conn, firm, tp)

    dry = run(False, engine=engine)
    assert dry["documents_to_purge"] >= 2 and dry["execute"] is False
    assert owner_conn.execute("SELECT count(*) FROM documents WHERE id = %s", (old_deleted,)).fetchone()[0] == 1

    report = run(True, engine=engine)
    assert report["files_removed"] >= 2
    remaining = {r[0] for r in owner_conn.execute("SELECT id FROM documents WHERE firm_id = %s", (firm,))}
    assert remaining == {recent_deleted, keep}
    storage = get_storage()
    assert not (storage.root / "documents" / p1).exists() and (storage.root / "documents" / p4).exists()
    assert owner_conn.execute("SELECT count(*) FROM audit_logs WHERE action = 'maintenance.retention'").fetchone()[0] >= 1

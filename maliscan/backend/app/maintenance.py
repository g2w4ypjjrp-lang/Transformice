"""Data retention maintenance (run daily from cron / the `maintenance` service).

    python -m app.maintenance              # dry run: report only
    python -m app.maintenance --execute    # actually delete

Policy (docs/SECURITY_KVKK.md):
  * Soft-deleted documents that were never approved/exported are purged
    (file + database rows) after SOFT_DELETE_GRACE_DAYS (30).
  * Documents past `retention_until` (legal retention, default 10 years) are
    purged together with their extraction data; approved journal entries are
    retained as accounting records but detached from the document.
  * Raw OCR text is cleared after RAW_TEXT_DAYS (90) — structured data stays.
  * Finished processing jobs older than 90 days are removed.
Every run writes one audit entry with counts only.
"""

from __future__ import annotations

import argparse
import json
import logging

from sqlalchemy import text

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import get_engine, service_transaction
from app.services.storage import StorageError, get_storage

log = logging.getLogger("maliscan.maintenance")
SOFT_DELETE_GRACE_DAYS = 30
RAW_TEXT_DAYS = 90
JOB_DAYS = 90


def run(execute: bool, engine=None, storage=None) -> dict:
    engine = engine or get_engine()
    storage = storage or get_storage()
    report = {"execute": execute}
    with service_transaction(engine) as conn:
        purge = conn.execute(text("""
            SELECT id, firm_id, storage_bucket, storage_path FROM public.documents
             WHERE (deleted_at IS NOT NULL AND deleted_at < now() - make_interval(days => :g)
                    AND status NOT IN ('approved','exported'))
                OR (retention_until IS NOT NULL AND retention_until < current_date)
             ORDER BY created_at LIMIT 5000"""), {"g": SOFT_DELETE_GRACE_DAYS}).mappings().all()
        report["documents_to_purge"] = len(purge)
        report["raw_text_to_clear"] = conn.execute(text("""
            SELECT count(*) FROM public.extracted_data WHERE raw_text IS NOT NULL
               AND created_at < now() - make_interval(days => :d)"""), {"d": RAW_TEXT_DAYS}).scalar_one()
        report["jobs_to_delete"] = conn.execute(text("""
            SELECT count(*) FROM public.processing_jobs WHERE status IN ('succeeded','dead')
               AND updated_at < now() - make_interval(days => :d)"""), {"d": JOB_DAYS}).scalar_one()
        if not execute:
            return report

        removed_files = 0
        for d in purge:
            conn.execute(text("""UPDATE public.journal_entries SET document_id = NULL, extracted_data_id = NULL
                                 WHERE firm_id = :f AND document_id = :d AND status IN ('approved','exported')"""),
                         {"f": d["firm_id"], "d": d["id"]})
            conn.execute(text("DELETE FROM public.journal_entries WHERE firm_id = :f AND document_id = :d"),
                         {"f": d["firm_id"], "d": d["id"]})
            conn.execute(text("DELETE FROM public.documents WHERE firm_id = :f AND id = :d"),
                         {"f": d["firm_id"], "d": d["id"]})
            try:
                storage.delete(d["storage_bucket"], d["storage_path"])
                removed_files += 1
            except StorageError:
                log.warning("file delete failed", extra={"event": "retention.file_delete_failed"})
        conn.execute(text("""UPDATE public.extracted_data SET raw_text = NULL WHERE raw_text IS NOT NULL
                               AND created_at < now() - make_interval(days => :d)"""), {"d": RAW_TEXT_DAYS})
        conn.execute(text("""DELETE FROM public.processing_jobs WHERE status IN ('succeeded','dead')
                               AND updated_at < now() - make_interval(days => :d)"""), {"d": JOB_DAYS})
        report["files_removed"] = removed_files
        conn.execute(text("""INSERT INTO public.audit_logs (firm_id, actor_user_id, action, entity_type, metadata)
                             VALUES (NULL, NULL, 'maintenance.retention', NULL, CAST(:m AS jsonb))"""),
                     {"m": json.dumps(report)})
    return report


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--execute", action="store_true", help="perform deletions (default: dry run)")
    args = p.parse_args()
    configure_logging(get_settings().log_level)
    print(json.dumps(run(args.execute)))


if __name__ == "__main__":
    main()

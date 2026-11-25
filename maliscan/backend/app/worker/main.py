"""Background worker: PostgreSQL job queue with FOR UPDATE SKIP LOCKED.

    python -m app.worker.main

Several workers can run in parallel. Failed jobs are retried with exponential
backoff (retryable errors only) and end as 'dead' after max_attempts. Jobs
stuck in 'running' (crashed worker) are re-queued after 15 minutes.
"""

from __future__ import annotations

import logging
import signal
import threading
import time
import uuid
from datetime import timedelta

from sqlalchemy import Engine, text

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.session import get_engine, service_transaction
from app.services.extraction.claude import ClaudeExtractor
from app.services.pipeline import DocumentGone, ProcessingError, build_journal, process_document_unlocked
from app.services.storage import Storage, get_storage

log = logging.getLogger("maliscan.worker")
STALE_AFTER = timedelta(minutes=15)


def claim_job(engine: Engine, worker_id: str, firm_id: uuid.UUID | str | None = None) -> dict | None:
    """Claim the next due job (optionally restricted to one firm, e.g. dedicated workers)."""
    with service_transaction(engine) as conn:
        conn.execute(text("""UPDATE public.processing_jobs SET status = 'queued', locked_by = NULL, locked_at = NULL
                             WHERE status = 'running' AND locked_at < now() - make_interval(mins => 15)"""))
        row = conn.execute(text("""
            UPDATE public.processing_jobs SET status = 'running', attempts = attempts + 1,
                   locked_by = :w, locked_at = now()
             WHERE id = (SELECT id FROM public.processing_jobs
                          WHERE status = 'queued' AND run_after <= now()
                            AND (CAST(:firm AS uuid) IS NULL OR firm_id = :firm)
                          ORDER BY priority, run_after
                          FOR UPDATE SKIP LOCKED LIMIT 1)
            RETURNING id, firm_id, document_id, job_type, attempts, max_attempts, payload"""),
            {"w": worker_id, "firm": firm_id}).mappings().one_or_none()
        return dict(row) if row else None


def run_job(job: dict, *, engine: Engine, storage: Storage, settings: Settings,
            claude: ClaudeExtractor | None = None) -> str:
    """Execute one claimed job; returns final job status."""
    firm_id, doc_id = job["firm_id"], job["document_id"]
    try:
        with service_transaction(engine) as conn:
            if job["job_type"] == "extract_document":
                conn.execute(text("UPDATE public.documents SET status = 'processing' WHERE firm_id = :f AND id = :d "
                                  "AND status IN ('queued','uploaded','failed') AND deleted_at IS NULL"), {"f": firm_id, "d": doc_id})
        if job["job_type"] not in ("extract_document", "generate_journal"):
            raise ProcessingError("unknown_job_type", job["job_type"])
        if job["job_type"] == "extract_document":
            # slow OCR/AI runs outside any transaction (no row lock → users can delete meanwhile)
            process_document_unlocked(engine, firm_id=firm_id, document_id=doc_id, storage=storage,
                                      settings=settings, claude=claude)
        with service_transaction(engine) as conn:
            if job["job_type"] == "generate_journal":
                build_journal(conn, firm_id=firm_id, document_id=doc_id, created_by=None)
                conn.execute(text("""UPDATE public.documents SET status = 'needs_review' WHERE firm_id = :f
                                     AND id = :d AND status IN ('processing','queued')"""),
                             {"f": firm_id, "d": doc_id})
            conn.execute(text("""UPDATE public.processing_jobs SET status = 'succeeded', finished_at = now(),
                                 last_error_code = NULL WHERE id = :j"""), {"j": job["id"]})
        log.info("job succeeded", extra={"event": "job.succeeded", "firm_id": firm_id})
        return "succeeded"
    except DocumentGone:
        # deleted/booked by a user meanwhile: finish the job without touching the document
        with service_transaction(engine) as conn:
            conn.execute(text("""UPDATE public.processing_jobs SET status = 'succeeded', finished_at = now(),
                                 last_error_code = 'skipped_document_gone', locked_by = NULL, locked_at = NULL
                                 WHERE id = :j"""), {"j": job["id"]})
        return "skipped"
    except Exception as exc:  # noqa: BLE001 — every failure must be recorded
        code = exc.code if isinstance(exc, ProcessingError) else type(exc).__name__
        retryable = exc.retryable if isinstance(exc, ProcessingError) else True
        final = (not retryable) or job["attempts"] >= job["max_attempts"]
        status = "dead" if final else "queued"
        delay = 30 * (2 ** (job["attempts"] - 1))
        with service_transaction(engine) as conn:
            conn.execute(text("""UPDATE public.processing_jobs SET status = :s, last_error_code = :c,
                                    locked_by = NULL, locked_at = NULL,
                                    run_after = now() + make_interval(secs => :delay),
                                    finished_at = CASE WHEN :final THEN now() END
                                 WHERE id = :j"""),
                         {"s": status, "c": code[:100], "delay": delay, "final": final, "j": job["id"]})
            conn.execute(text("""UPDATE public.documents SET status = :st, error_code = :c
                                 WHERE firm_id = :f AND id = :d AND status NOT IN ('approved','exported')
                                   AND deleted_at IS NULL"""),
                         {"st": "failed" if final else "queued", "c": code[:100], "f": firm_id, "d": doc_id})
        log.warning("job failed", extra={"event": "job.failed", "error_code": code, "firm_id": firm_id})
        return status


def run_loop(stop: "threading.Event", *, settings: Settings | None = None) -> None:
    """Poll-and-process loop; used by the standalone worker and the API's embedded worker."""
    settings = settings or get_settings()
    engine, storage = get_engine(), get_storage()
    worker_id = f"{settings.worker_id}-{uuid.uuid4().hex[:6]}"
    log.info("worker started", extra={"event": "worker.started"})
    while not stop.is_set():
        try:
            job = claim_job(engine, worker_id)
        except Exception:  # noqa: BLE001 — DB hiccup: back off
            log.exception("claim failed")
            stop.wait(5)
            continue
        if job is None:
            stop.wait(settings.worker_poll_seconds)
            continue
        run_job(job, engine=engine, storage=storage, settings=settings)
    log.info("worker stopped", extra={"event": "worker.stopped"})


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    run_loop(stop, settings=settings)


if __name__ == "__main__":
    main()

"""MaliScan AI API entrypoint."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import install_error_handlers
from app.api.routes import admin, dashboard, documents, exports, files, firms, health, me, members, taxpayers
from app.core.config import get_settings
from app.core.logging import configure_logging, request_id_var

log = logging.getLogger("maliscan.http")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    docs_enabled = settings.environment != "production"

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        stop = threading.Event()
        thread = None
        if settings.run_migrations_on_startup:
            from app.db.migrate import run as migrate

            applied = migrate(settings.database_url.get_secret_value())
            log.info("migrations checked", extra={"event": "migrations.startup", "status_code": len(applied)})
        if settings.storage_backend == "supabase":
            from app.services.storage import get_storage

            try:
                get_storage().ensure_buckets(["documents", "exports"])  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001 — storage misconfiguration must not stop the API
                log.exception("bucket check failed")
        if settings.embedded_worker:
            from app.worker.main import run_loop

            thread = threading.Thread(target=run_loop, args=(stop,), kwargs={"settings": settings},
                                      name="embedded-worker", daemon=True)
            thread.start()
        yield
        stop.set()
        if thread:
            thread.join(timeout=30)

    app = FastAPI(
        lifespan=lifespan,
        title="MaliScan AI API",
        version="0.1.0",
        description="Multi-tenant document processing and accounting API for Turkish SMMM offices.",
        docs_url=f"{settings.api_prefix}/docs" if docs_enabled else None,
        redoc_url=f"{settings.api_prefix}/redoc" if docs_enabled else None,
        openapi_url=f"{settings.api_prefix}/openapi.json" if docs_enabled else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,  # bearer tokens, no cookies
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["Content-Disposition", "X-Request-ID"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        rid = rid[:64]
        token = request_id_var.set(rid)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = rid
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        log.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,  # path params are UUIDs only, no PII
                "status_code": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )
        return response

    install_error_handlers(app)
    for module in (health, me, firms, members, taxpayers, documents, exports, dashboard, admin, files):
        app.include_router(module.router, prefix=settings.api_prefix)
    return app


app = create_app()

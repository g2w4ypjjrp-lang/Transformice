from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.db.session import get_engine

router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness probe")
def health() -> dict:
    return {"status": "ok"}


@router.get("/health/ready", summary="Readiness probe (database reachable, migrations applied)")
def ready() -> JSONResponse:
    try:
        with get_engine().connect() as conn:
            version = conn.execute(text("SELECT max(version) FROM app.schema_migrations")).scalar_one()
    except Exception:  # noqa: BLE001 — readiness must never raise
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return JSONResponse({"status": "ready", "schema_version": version})

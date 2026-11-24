"""Structured JSON logging.

Rule: log identifiers and outcomes only. Never log request bodies, document
content, amounts, names or tax numbers (KVKK / spec rule 12).
"""

import json
import logging
import sys
from contextvars import ContextVar

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        rid = request_id_var.get()
        if rid:
            payload["request_id"] = rid
        for key in ("event", "firm_id", "user_id", "status_code", "duration_ms", "path", "method", "error_code"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = str(value) if not isinstance(value, (int, float)) else value
        if record.exc_info:
            payload["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # SQL echo would print parameters (financial data) — keep it silent.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

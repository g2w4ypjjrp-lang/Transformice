"""Small shared helpers for route modules."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import Connection, text


def update_row(conn: Connection, table: str, changes: dict[str, Any], where: str, params: dict) -> int:
    """UPDATE with whitelisted column names (keys come from Pydantic models only)."""
    if not changes:
        return 0
    cols = []
    values: dict[str, Any] = {}
    for i, (k, v) in enumerate(changes.items()):
        if not k.isidentifier():
            raise ValueError("invalid column")
        if isinstance(v, (dict, list)):
            cols.append(f"{k} = CAST(:v{i} AS jsonb)")
            v = json.dumps(v)
        else:
            cols.append(f"{k} = :v{i}")
        values[f"v{i}"] = v
    res = conn.execute(text(f"UPDATE public.{table} SET {', '.join(cols)} WHERE {where}"), {**values, **params})
    return res.rowcount


def audit(conn: Connection, firm_id: uuid.UUID | None, action: str, entity_type: str | None,
          entity_id: uuid.UUID | None, metadata: dict | None = None) -> None:
    """Audit entries carry identifiers/counts only — never amounts, names or document text."""
    conn.execute(
        text("SELECT app.write_audit_log(:f, :a, :t, :e, CAST(:m AS jsonb))"),
        {"f": firm_id, "a": action, "t": entity_type, "e": entity_id, "m": json.dumps(metadata or {})},
    )

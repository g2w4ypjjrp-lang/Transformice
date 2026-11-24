"""Ordered SQL migration runner.

Applies `database/migrations/NNNN_*.sql` in order, each in its own transaction,
recording version + SHA-256 checksum in `app.schema_migrations`. A modified,
already-applied migration aborts the run. A PostgreSQL advisory lock prevents
concurrent runners (e.g. several containers starting at once).

Usage:
    python -m app.db.migrate                # production / Supabase
    python -m app.db.migrate --with-shim    # local & CI: also create Supabase auth shim
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from pathlib import Path

import psycopg

_ADVISORY_LOCK_KEY = 735_120_001
_FILE_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


def database_dir() -> Path:
    env = os.environ.get("MALISCAN_DATABASE_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "database"


def _plain_dsn(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def discover(directory: Path) -> list[tuple[str, Path, str]]:
    found = []
    for path in sorted(directory.glob("*.sql")):
        m = _FILE_RE.match(path.name)
        if not m:
            raise RuntimeError(f"unexpected migration file name: {path.name}")
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        found.append((m.group(1), path, checksum))
    versions = [v for v, _, _ in found]
    if len(versions) != len(set(versions)):
        raise RuntimeError("duplicate migration versions")
    return found


def run(dsn: str, with_shim: bool = False, directory: Path | None = None) -> list[str]:
    base = directory or database_dir()
    applied_now: list[str] = []
    with psycopg.connect(_plain_dsn(dsn), autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (_ADVISORY_LOCK_KEY,))
        try:
            if with_shim:
                conn.execute((base / "bootstrap" / "local_supabase_shim.sql").read_text())
            with conn.transaction():
                conn.execute("CREATE SCHEMA IF NOT EXISTS app")
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS app.schema_migrations (
                         version    text PRIMARY KEY,
                         filename   text NOT NULL,
                         checksum   char(64) NOT NULL,
                         applied_at timestamptz NOT NULL DEFAULT now())"""
                )
            applied = {
                row[0]: row[1]
                for row in conn.execute("SELECT version, checksum FROM app.schema_migrations")
            }
            for version, path, checksum in discover(base / "migrations"):
                if version in applied:
                    if applied[version] != checksum:
                        raise RuntimeError(
                            f"migration {path.name} was modified after being applied; "
                            "create a new migration instead"
                        )
                    continue
                with conn.transaction():
                    conn.execute(path.read_text())
                    conn.execute(
                        "INSERT INTO app.schema_migrations (version, filename, checksum) VALUES (%s, %s, %s)",
                        (version, path.name, checksum),
                    )
                applied_now.append(path.name)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (_ADVISORY_LOCK_KEY,))
    return applied_now


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--with-shim", action="store_true", help="apply local Supabase auth shim first")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    args = parser.parse_args(argv)
    if not args.database_url:
        print("DATABASE_URL is required", file=sys.stderr)
        return 2
    applied = run(args.database_url, with_shim=args.with_shim)
    for name in applied:
        print(f"applied {name}")
    print(f"{len(applied)} migration(s) applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

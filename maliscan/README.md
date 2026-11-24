# MaliScan AI

Multi-tenant SaaS for Turkish certified public accountants (SMMM) using Luca:
live camera scanning of receipts and invoices → OCR / e-document parsing →
AI-assisted classification → deterministic journal generation → human
approval → Luca-compatible Excel export.

> **Status: Milestone 1 (Foundation) complete.** See
> [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) for the
> architecture and milestone roadmap. Not yet usable end-to-end.

## Layout

```
maliscan/
├── backend/                FastAPI service (Python 3.12+)
│   ├── app/
│   │   ├── api/            routes, dependencies (auth, tenant DB session), error mapping
│   │   ├── core/           settings, JWT verification, structured logging
│   │   ├── db/             engine, RLS-scoped transactions, migration runner
│   │   └── schemas/        Pydantic request/response models
│   └── tests/              pytest suite (runs against real PostgreSQL)
├── database/
│   ├── migrations/         ordered SQL migrations (Supabase compatible)
│   └── bootstrap/          local-only Supabase auth shim for dev/CI
├── docs/                   plan, API reference
├── docker-compose.dev.yml  local PostgreSQL
└── .env.example
```

## Quick start (local)

Requirements: Python 3.12+, PostgreSQL 16 (or Docker).

```bash
cd maliscan
docker compose -f docker-compose.dev.yml up -d        # or use a local PostgreSQL 16

cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp ../.env.example .env                               # set SUPABASE_JWT_SECRET for local tokens

export DATABASE_URL=postgresql+psycopg://postgres:postgres@127.0.0.1:5432/maliscan
python -m app.db.migrate --with-shim                  # --with-shim ONLY outside Supabase
uvicorn app.main:app --reload
# → http://127.0.0.1:8000/api/v1/docs
```

### Against Supabase

Apply migrations **without** `--with-shim` (Supabase already provides the
`auth` schema and roles), using the direct database connection string:

```bash
DATABASE_URL=postgresql://postgres:<password>@db.<ref>.supabase.co:5432/postgres \
  python -m app.db.migrate
```

## Tests

```bash
cd backend
TEST_DATABASE_ADMIN_URL=postgresql://postgres:postgres@127.0.0.1:5432/postgres pytest
```

The suite creates a throwaway database, applies the shim + all migrations and
drops it afterwards. It covers migrations, tenant isolation (RLS), role
permissions, accounting invariants, authentication and the HTTP API.

## Security model (summary)

* Supabase Auth issues JWTs; the API verifies signature/expiry/audience (HS256 secret or JWKS).
* Each request runs as PostgreSQL role `authenticated` with the user's claims, so **RLS policies
  are enforced on every query**, in addition to application-level role checks.
* Composite `(firm_id, id)` foreign keys make cross-tenant references impossible at the schema level.
* Accounting invariants (balanced entries, single-sided lines, immutable approved entries, locked
  periods, state machine) are enforced by database triggers and constraints.
* Audit log and approval history are append-only (even for the DB owner).
* Logs contain identifiers only — never amounts, names, tax numbers or document text.
* Secrets come only from environment variables; nothing is exposed to the frontend.

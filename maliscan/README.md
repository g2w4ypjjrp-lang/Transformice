# MaliScan AI

Multi-tenant SaaS for Turkish certified public accountants (SMMM) using Luca:
live camera scanning of receipts and invoices → e-invoice XML parsing / AI
extraction → deterministic journal generation → human approval →
Luca-compatible import files.

| Scanner | Review | Dashboard |
|---|---|---|
| ![](docs/screenshots/scanner.png) | ![](docs/screenshots/review.png) | ![](docs/screenshots/dashboard.png) |

## Status — ready for a controlled pilot, with three open items

All ten milestones of [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md)
are implemented and tested (see *Tests* below). Before real customer data:

1. **Luca import format is UNVERIFIED.** The template is reconstructed from
   Luca's official support article; the official "Şablon İndir" file and a real
   import test are still needed → [docs/LUCA_INTEGRATION.md §6](docs/LUCA_INTEGRATION.md).
2. **AI extraction has not run against the real Claude API in this repository's
   tests** (no API key in CI; a fake client is used). Run the pilot validation
   with synthetic receipts first and measure accuracy.
3. **KVKK paperwork** (DPAs, standard contracts for cross-border transfer) →
   [docs/SECURITY_KVKK.md](docs/SECURITY_KVKK.md).

## Features

* **Offices & roles** — owner, admin, accountant, clerk, viewer; e-mail invitations; plan limits.
* **Taxpayers** — VKN/TCKN validation, bookkeeping basis, locked periods, default accounts,
  TDHP chart seed, Luca chart import (CSV/XLSX), account mappings (incl. learned), declarative rules.
* **Live scanner (PWA)** — camera with document boundary detection, multi-receipt, stability
  auto-capture, perspective correction, enhancement (gray/color/B&W), blur warning, torch,
  upload queue with retry, file/photo fallback.
* **Processing** — UBL-TR e-Fatura/e-Arşiv XML parsed directly (no AI); PDFs with embedded XML
  use the XML; images/PDFs extracted by Claude (structured JSON, never-invent rules) with optional
  OCR pre-pass; deterministic validation (VAT arithmetic, totals, rates, dates, tax-number checksums)
  and confidence scoring; duplicate detection (exact file + business fingerprint).
* **Accounting engine** — rules → mappings → defaults; multi-rate VAT, KDV tevkifatı, KKEG /
  non-deductible VAT, cash/bank/card/supplier payments, sales invoices; always balanced drafts.
* **Approval** — review screen with document image, corrections (versioned), journal editing,
  approve/reject/reopen, bulk approval with safety skips, history. DB refuses unbalanced or
  locked-period approvals.
* **Luca export** — validated, ≤ 50 vouchers per file, CSV (;, Windows-1254) or XLSX, export
  history, duplicate-export prevention, signed download links.
* **Dashboard & super admin** — office statistics; platform offices/users/plans/jobs/errors/config.
* **Operations** — Docker images, compose stack (API, 2 workers, web, nginx+TLS, certbot,
  encrypted backups, retention maintenance), migrations, OpenAPI docs.

## Layout

```
maliscan/
├── backend/            FastAPI API + worker (Python 3.12)
│   ├── app/api         routes (66 endpoints), auth/tenant deps, errors
│   ├── app/services    storage, files, extraction (UBL, Claude, OCR, validation),
│   │                   accounting engine, pipeline, luca export
│   ├── app/worker      PostgreSQL job queue worker
│   ├── scripts         sample generators, OpenAPI export, DEV-only auth stub & E2E worker
│   └── tests           pytest (real PostgreSQL)
├── web/                Next.js 14 PWA (TypeScript, Tailwind) + scanner library + E2E
├── database/           SQL migrations (Supabase-compatible) + local auth shim
├── deploy/             nginx template, backup/restore scripts
├── samples/luca/       synthetic Luca pilot validation pack
├── docs/               plan, API, deployment, backup, security/KVKK, Luca report
└── docker-compose.yml  production stack
```

## Local development

```bash
# database
docker compose -f docker-compose.dev.yml up -d
cd backend && python -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt
export DATABASE_URL=postgresql+psycopg://postgres:postgres@127.0.0.1:5432/maliscan \
       SUPABASE_JWT_SECRET=dev-secret-dev-secret-dev-secret-0001 ENVIRONMENT=development
python -m app.db.migrate --with-shim
uvicorn app.main:app --reload --port 8000            # API  → /api/v1/docs
python -m app.worker.main                            # worker (needs ANTHROPIC_API_KEY for images/PDFs)
uvicorn scripts.dev_auth_stub:app --port 9999        # DEV ONLY: local stand-in for Supabase Auth

cd ../web && npm install
NEXT_PUBLIC_API_URL=http://localhost:8000 NEXT_PUBLIC_SUPABASE_URL=http://localhost:9999 \
NEXT_PUBLIC_SUPABASE_ANON_KEY=dev npm run dev       # → http://localhost:3000
```

With a real Supabase project, point `NEXT_PUBLIC_SUPABASE_URL`/`SUPABASE_URL` at it instead of the stub.

## Tests

| Suite | Command | Result (2026-11-24) |
|---|---|---|
| Backend (migrations, RLS/tenant isolation, roles, accounting invariants, auth, API, masterdata, plan limits, UBL, validation, Claude extractor (fake client), OCR, storage, engine, full document flow, Luca export, admin, retention) | `cd backend && pytest` | **184 passed** |
| Scanner library (geometry, detection, multi-receipt, stability, enhancement) | `cd web && npm test` | **11 passed** |
| Type check / lint / production build | `npm run typecheck && npm run lint && npm run build` | clean |
| Browser E2E on the static (Cloudflare-style) build (landing/download page → register → office → taxpayer → **live camera auto-capture** → review → approve → Luca export → dashboard → mobile) | `node web/e2e/smoke.mjs` with the stack running | **passed** |
| Docker images, container smoke test, nginx config test | see docs/DEPLOYMENT.md | passed |
| Backup → restore drill | see docs/BACKUP_RECOVERY.md | passed (byte-identical) |

## Documentation

* **[Ücretsiz kurulum (VPS'siz): Cloudflare Pages + Render + Supabase → luca.canarslan.net.tr](docs/UCRETSIZ_KURULUM.md)**

* [Implementation plan & architecture](docs/IMPLEMENTATION_PLAN.md)
* [API reference](docs/API.md) · [openapi.json](docs/openapi.json)
* [Luca integration research & pilot protocol](docs/LUCA_INTEGRATION.md)
* [VPS deployment (alternative)](docs/DEPLOYMENT.md)
* [Backup & recovery](docs/BACKUP_RECOVERY.md)
* [Security, KVKK & retention](docs/SECURITY_KVKK.md)

# MaliScan AI — Architecture & Implementation Plan

## 1. Repository decision

The host repository contains an unrelated Java game server. MaliScan AI lives in
the self-contained `maliscan/` directory and shares nothing with the rest of the
repository. It can be extracted into its own repository at any time:

```bash
git subtree split --prefix=maliscan -b maliscan-only
```

## 2. High-level architecture

```
 ┌──────────────────────┐        HTTPS (Nginx)        ┌──────────────────────────┐
 │ Next.js PWA (web/)   │ ──────────────────────────▶ │ FastAPI (backend/)       │
 │ - Supabase Auth JS   │   Authorization: Bearer JWT │ - JWT verification       │
 │ - Camera scanner     │                             │ - Tenant-scoped DB tx    │
 │ - Review UI          │                             │ - Accounting engine      │
 └─────────┬────────────┘                             │ - Luca Excel export      │
           │ login / refresh only                     └──────┬─────────┬─────────┘
           ▼                                                 │         │ enqueue
 ┌──────────────────────┐                                    ▼         ▼
 │ Supabase Auth        │                       ┌────────────────┐ ┌────────────────────┐
 └──────────────────────┘                       │ PostgreSQL     │ │ Worker (backend/)  │
                                                │ + RLS policies │◀│ OCR → Claude →     │
 ┌──────────────────────┐   signed URLs only    │ + job queue    │ │ validation → draft │
 │ Supabase Storage     │◀──────────────────────┴────────────────┘ └────────────────────┘
 └──────────────────────┘
```

Key decisions

| Topic | Decision | Reason |
|---|---|---|
| Auth | Supabase Auth issues JWTs; backend verifies them (HS256 secret or JWKS). | No password handling in our code. |
| Tenant isolation | **Two layers**: application authorization *and* PostgreSQL RLS. Each API request runs in a transaction with `SET LOCAL ROLE authenticated` and `request.jwt.claims` set, exactly like Supabase PostgREST. | A bug in API code cannot leak another office's data. |
| Background jobs | PostgreSQL-backed queue (`processing_jobs`, `FOR UPDATE SKIP LOCKED`). | No extra infrastructure (Redis) for pilot scale; transactional with document state. |
| Migrations | Plain, ordered SQL files in `database/migrations/`. Compatible with `supabase db push`. A local *shim* recreates Supabase's `auth` schema and roles for dev/CI. | Same SQL in tests, local and production. |
| Money | `numeric(18,2)` in DB, `decimal.Decimal` in Python. Never floats. | Accounting correctness. |
| AI | Claude returns structured JSON validated by Pydantic; deterministic rules validate every journal. AI output is a *suggestion*. | Spec: no automatic final accounting. |
| OCR | Provider interface (`OcrProvider`) with implementations selected by env. E-documents (UBL-TR XML) parsed directly, bypassing OCR. | Prefer original electronic data. |
| Luca | Excel generation against a versioned template definition; 50-voucher cap per file; no credentials, no browser automation. | Spec + legal safety. |
| Files | Private storage bucket; frontend only ever receives short-lived signed URLs. | Spec security requirements. |
| Logs | Structured logs with IDs only — no amounts, names, tax numbers or OCR text. | KVKK / spec rule 12. |

## 3. Roles

| Role | Scope | Capabilities |
|---|---|---|
| `super_admin` | platform | Everything in the Super Admin panel; flag on `users`. |
| `owner` | firm | Billing, members, all data. One or more per firm. |
| `admin` | firm | Members (except owners), taxpayers, rules, all data. |
| `accountant` | firm | Taxpayers, documents, approve journals, export. |
| `clerk` | firm | Upload/scan documents, correct OCR; **cannot approve or export**. |
| `viewer` | firm | Read-only. |

## 4. Document lifecycle

```
uploaded → queued → processing → needs_review → approved → exported
                         │              │
                         ▼              ▼
                      failed        rejected
          (duplicate detection may flag at needs_review)
```

Journal entries: `draft → approved → exported` (or `rejected`). A DB trigger
refuses to mark an entry `approved` unless Σdebit = Σcredit > 0, and refuses
any modification of lines once approved.

## 5. Milestones

Each milestone ends with: tests run, report, known issues, remaining tasks,
**wait for approval**.

| # | Milestone | Content |
|---|---|---|
| | **Status (2026-11-24)** | **All milestones implemented and tested. Open: real Luca import verification, real Claude accuracy measurement, KVKK contracts — see README.** |
| **M1** | Foundation | Plan, DB schema + migrations, RLS, Supabase shim, FastAPI skeleton, JWT auth, tenant-scoped sessions, firm bootstrap, `/me`, audit log, migration runner, DB + API tests. |
| M2 | Offices, members, taxpayers | Invitations, role management, taxpayers CRUD, VKN/TCKN validation, chart of accounts import (Tek Düzen Hesap Planı seed), account mappings, accounting rules CRUD, subscription limits enforcement. |
| M3 | Documents & storage | Upload API (type/size/magic-byte validation), Supabase Storage + signed URLs, SHA-256 dedupe, job queue + worker, UBL-TR XML parser, PDF text extraction. |
| M4 | OCR + Claude extraction | OCR provider abstraction (+ implementation), Claude structured extraction, Pydantic schema, confidence scoring, arithmetic cross-checks, synthetic Turkish receipts/invoices fixtures. |
| M5 | Accounting engine | Rule evaluation, expense classification, VAT (1/10/20 %), KDV tevkifatı, KKEG / non-deductible VAT, cash/bank/supplier handling, balancing, duplicate detection, journal draft generation. |
| M6 | Approval workflow | Review API, corrections (versioned extracted_data), journal editing, approve/reject, approval history. |
| M7 | Luca export | Template definition, Excel generation (openpyxl), mandatory-field validation, 50-voucher batching, export history, duplicate-export prevention. |
| M8 | Frontend PWA | Next.js + Tailwind app: auth, dashboard, taxpayers, live camera scanner (edge detection, stability auto-capture, multi-receipt, enhancement, fallback), review UI, exports. |
| M9 | Dashboard & Super Admin | Statistics endpoints/views, admin panel: offices, users, plans, usage, jobs, errors, platform config. |
| M10 | Deployment | Dockerfiles, docker-compose, Nginx + HTTPS, backups (pg_dump + storage), restore drill docs, VPS guide, KVKK & retention docs, API docs. |

## 6. Open items requiring business input

1. **Official Luca import template** — exact column layout/version must come from
   Luca's official documentation or a sample file exported by a pilot office. M7
   is built around a versioned template definition so it can be swapped without
   code changes.
2. **OCR provider choice** (Google Vision, Azure Document Intelligence, AWS
   Textract…) — affects KVKK (cross-border transfer) analysis and DPA contracts.
3. **Anthropic API data processing agreement** for KVKK compliance.
4. Subscription plan prices/limits.

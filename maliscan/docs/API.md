# MaliScan AI — API Reference

Base path: `/api/v1`. Interactive OpenAPI docs: `/api/v1/docs` (disabled when
`ENVIRONMENT=production`).

## Conventions

* **Authentication**: `Authorization: Bearer <Supabase access token>` on every
  endpoint except health probes.
* **Tenant isolation**: each request runs in one PostgreSQL transaction as role
  `authenticated` with the token's claims, so RLS applies to every query.
  Resources of firms you do not belong to return **404** (never 403) so other
  tenants' existence is not disclosed.
* **Errors**: always
  ```json
  {"error": {"code": "forbidden", "message": "insufficient permissions"}}
  ```
  | HTTP | code | meaning |
  |---|---|---|
  | 401 | `unauthenticated` | missing/invalid/expired token |
  | 403 | `forbidden` | role lacks permission |
  | 403 | `profile_missing` / `user_disabled` | account state |
  | 404 | `not_found` | not found or not visible to you |
  | 409 | `conflict` | unique constraint |
  | 422 | `validation_error` | request body/params invalid (`fields` lists locations) |
  | 422 | `constraint_violation`, `invalid_reference`, `invalid_value` | business rule enforced by DB |
* Every response carries `X-Request-ID` (echoes the request header if sent).

## Roles

`owner`, `admin`, `accountant`, `clerk`, `viewer` — see
[IMPLEMENTATION_PLAN.md §3](IMPLEMENTATION_PLAN.md#3-roles).


## Workflow summary

1. `POST /firms` → office (caller = owner, 14-day trial).
2. `POST /firms/{id}/taxpayers` → taxpayer (+ default TDHP chart).
3. `POST /firms/{id}/taxpayers/{tp}/documents` (multipart `file`, `source`) → status `queued`.
4. Worker: UBL-TR XML parsed directly; images/PDFs extracted by Claude → validation →
   draft journal → status `needs_review`.
5. `GET /firms/{id}/documents/{doc}` → review payload (signed file URL, extraction, issues, journal).
6. Optional: `PUT …/extraction` (correction, new version) / `PUT …/journal` (manual lines).
7. `POST …/approve` (owner/admin/accountant; DB enforces balance, period lock, role).
8. `POST /firms/{id}/taxpayers/{tp}/exports` → Luca files (≤ 50 vouchers each; template UNVERIFIED).

## Errors specific to later milestones

| HTTP | code | meaning |
|---|---|---|
| 402 | `plan_limit_exceeded` / `subscription_inactive` | subscription limits (DB-enforced) |
| 409 | `duplicate_file` | identical file already uploaded for the taxpayer |
| 409 | `possible_duplicate` | approval needs `acknowledge_duplicate: true` |
| 409 | `document_locked` / `journal_locked` / `invalid_state` | workflow state does not allow the action |
| 413 / 415 | `file_too_large` / `unsupported_type` | upload validation |
| 422 | `unbalanced`, `account_not_in_chart`, `export_validation_failed` (with `issues`) | accounting validation |

## Endpoints

<!-- BEGIN GENERATED ENDPOINTS (scripts/export_openapi.py) -->
_66 endpoints, generated from the FastAPI app; full schemas in [openapi.json](openapi.json)._

### health

| Method | Path | Summary |
|---|---|---|
| GET | `/health` | Liveness probe |
| GET | `/health/ready` | Readiness probe (database reachable, migrations applied) |

### me

| Method | Path | Summary |
|---|---|---|
| GET | `/me` | Current user profile and firm memberships |
| PATCH | `/me` | Update own profile |

### firms

| Method | Path | Summary |
|---|---|---|
| POST | `/firms` | Create an accounting office; caller becomes owner, trial subscription starts |
| GET | `/firms` | Firms the caller is an active member of |
| GET | `/firms/{firm_id}` | Firm details with subscription |
| PATCH | `/firms/{firm_id}` | Update firm (owner/admin) |
| GET | `/firms/{firm_id}/members` | Firm members |
| GET | `/firms/{firm_id}/audit-logs` | Audit log (owner/admin) |
| GET | `/firms/{firm_id}/usage` | Subscription usage against plan limits |

### members

| Method | Path | Summary |
|---|---|---|
| POST | `/firms/{firm_id}/invitations` | Invite a user by e-mail (owner/admin). Returns a one-time token/link. |
| GET | `/firms/{firm_id}/invitations` | Invitations (owner/admin) |
| DELETE | `/firms/{firm_id}/invitations/{invitation_id}` | Revoke invitation |
| POST | `/invitations/accept` | Accept an invitation (must be signed in with the invited e-mail) |
| PATCH | `/firms/{firm_id}/members/{user_id}` | Change role or disable a member (owner/admin) |
| DELETE | `/firms/{firm_id}/members/{user_id}` | Remove a member (owner/admin). The last owner cannot be removed. |

### taxpayers

| Method | Path | Summary |
|---|---|---|
| GET | `/firms/{firm_id}/taxpayers` | List taxpayers with document counters |
| POST | `/firms/{firm_id}/taxpayers` | Create taxpayer (owner/admin/accountant) |
| GET | `/firms/{firm_id}/taxpayers/{taxpayer_id}` | Taxpayer details |
| PATCH | `/firms/{firm_id}/taxpayers/{taxpayer_id}` | Update taxpayer settings |
| DELETE | `/firms/{firm_id}/taxpayers/{taxpayer_id}` | Archive taxpayer (owner/admin; soft delete) |
| GET | `/firms/{firm_id}/taxpayers/{taxpayer_id}/accounts` | Chart of accounts |
| POST | `/firms/{firm_id}/taxpayers/{taxpayer_id}/accounts` | Add/overwrite account |
| PATCH | `/firms/{firm_id}/taxpayers/{taxpayer_id}/accounts/{account_id}` | Update account |
| POST | `/firms/{firm_id}/taxpayers/{taxpayer_id}/accounts/seed-default` | Add the default TDHP accounts (idempotent) |
| POST | `/firms/{firm_id}/taxpayers/{taxpayer_id}/accounts/import` | Import chart from CSV/XLSX (columns: Hesap Kodu, Hesap Adı) e.g. Luca hesap planı export |
| GET | `/firms/{firm_id}/taxpayers/{taxpayer_id}/mappings` | Account mappings |
| POST | `/firms/{firm_id}/taxpayers/{taxpayer_id}/mappings` | Create mapping |
| PUT | `/firms/{firm_id}/taxpayers/{taxpayer_id}/mappings/{mapping_id}` | Replace mapping |
| DELETE | `/firms/{firm_id}/taxpayers/{taxpayer_id}/mappings/{mapping_id}` | Delete mapping |
| GET | `/firms/{firm_id}/taxpayers/{taxpayer_id}/rules` | Accounting rules (taxpayer + firm-wide) |
| POST | `/firms/{firm_id}/taxpayers/{taxpayer_id}/rules` | Create rule |
| PUT | `/firms/{firm_id}/taxpayers/{taxpayer_id}/rules/{rule_id}` | Replace rule |
| DELETE | `/firms/{firm_id}/taxpayers/{taxpayer_id}/rules/{rule_id}` | Delete rule (owner/admin) |

### documents

| Method | Path | Summary |
|---|---|---|
| POST | `/firms/{firm_id}/taxpayers/{taxpayer_id}/documents` | Upload a receipt/invoice (JPEG/PNG/WEBP/PDF/UBL-TR XML) and queue it for processing |
| GET | `/firms/{firm_id}/documents` | List documents (filters + pagination) |
| GET | `/firms/{firm_id}/documents/{document_id}` | Full review payload: file URL, extraction, journal, history |
| DELETE | `/firms/{firm_id}/documents/{document_id}` | Delete (soft) a document not yet approved |
| GET | `/firms/{firm_id}/documents/{document_id}/versions` | All extraction versions (AI + corrections) |
| PUT | `/firms/{firm_id}/documents/{document_id}/extraction` | Save a corrected extraction (new version) and rebuild draft |
| PUT | `/firms/{firm_id}/documents/{document_id}/journal` | Replace the draft journal (manual edit; must balance) |
| POST | `/firms/{firm_id}/documents/{document_id}/approve` | Approve document + journal (owner/admin/accountant) |
| POST | `/firms/{firm_id}/documents/bulk-approve` | Approve many reviewed documents; skips duplicates, low confidence and error cases |
| POST | `/firms/{firm_id}/documents/{document_id}/reject` | Reject document (owner/admin/accountant) |
| POST | `/firms/{firm_id}/documents/{document_id}/reopen` | Reopen an approved (not exported) or rejected document |
| POST | `/firms/{firm_id}/documents/{document_id}/reprocess` | Run extraction again (not for approved/exported) |

### exports

| Method | Path | Summary |
|---|---|---|
| POST | `/firms/{firm_id}/taxpayers/{taxpayer_id}/exports` | Create Luca import files (≤50 vouchers per file) |
| GET | `/firms/{firm_id}/exports` | Export history |
| GET | `/firms/{firm_id}/exports/{export_id}` | Export detail with short-lived download links |

### dashboard

| Method | Path | Summary |
|---|---|---|
| GET | `/firms/{firm_id}/dashboard` | Document and processing statistics for the office |

### admin

| Method | Path | Summary |
|---|---|---|
| GET | `/admin/overview` | Platform usage overview |
| GET | `/admin/firms` | All offices |
| PATCH | `/admin/firms/{firm_id}` | Suspend / reactivate / close an office |
| PUT | `/admin/firms/{firm_id}/subscription` | Assign a plan to an office |
| GET | `/admin/users` | All users |
| PATCH | `/admin/users/{user_id}` | Disable a user / grant super admin |
| GET | `/admin/plans` | Subscription plans |
| PUT | `/admin/plans/{code}` | Create or update a plan |
| GET | `/admin/jobs` | Processing jobs monitor |
| POST | `/admin/jobs/{job_id}/retry` | Re-queue a dead/failed job |
| GET | `/admin/errors` | Error summary (failed documents and dead jobs by error code) |
| GET | `/admin/config` | Platform configuration |
| PUT | `/admin/config/{key}` | Set a platform configuration value |
| GET | `/admin/audit-logs` | Platform-wide audit log |

### files

| Method | Path | Summary |
|---|---|---|
| GET | `/files/{token}` | Signed file download (local storage backend only) |

<!-- END GENERATED ENDPOINTS -->

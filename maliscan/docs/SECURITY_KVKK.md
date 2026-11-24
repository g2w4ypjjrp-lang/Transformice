# Security, KVKK and Data Retention

> This document describes technical measures and the compliance work the
> operator must complete. It is **not legal advice**; have it reviewed by a
> KVKK-qualified lawyer before the pilot.

## 1. Data processed

| Category | Examples | Where |
|---|---|---|
| Identity / contact (users) | name, e-mail | Supabase Auth, `public.users` |
| Taxpayer data | title, VKN/TCKN, tax office, address | `taxpayers` |
| Financial documents | receipt/invoice images, PDFs, e-invoice XML | private storage bucket |
| Extracted financial data | amounts, VAT, suppliers, line items | `extracted_data`, `document_items` |
| Accounting records | journal entries, approvals, exports | DB + export files |
| Technical | audit log (IDs, actions), request IDs | DB, container logs |

Individual taxpayers' TCKN and personal purchases may constitute personal data
of third parties; documents can incidentally contain other personal data.
GPS/EXIF metadata is stripped from every uploaded image; GPS fields in client
capture metadata are dropped server-side.

## 2. Roles under KVKK

* **Accounting office (SMMM)**: data controller (veri sorumlusu) for its
  taxpayers' data.
* **MaliScan operator**: data processor (veri işleyen) → requires a written
  processing agreement with every office (scope, security measures,
  sub-processors, deletion on termination, breach notification).
* **Sub-processors**: Supabase (hosting/DB/auth/storage), Anthropic (AI
  extraction), optional OCR provider (e.g. Google Cloud Vision), VPS provider.

## 3. Cross-border transfer (KVKK md. 9) — action required

Sending document images to Anthropic and (if enabled) an OCR provider is a
transfer abroad. Since the 2024 amendment, transfers rely on adequacy
decisions, **standard contractual clauses (notified to the Authority within
5 business days of signature)**, binding corporate rules, or narrow
exceptions. Before processing real documents:

1. Sign the Anthropic commercial terms / DPA; review data retention settings
   for the API organisation.
2. Execute KVKK standard contracts with each foreign sub-processor and notify
   the Authority.
3. Update the offices' privacy notices (aydınlatma metni) to mention AI
   processing abroad; the registration screen already shows a consent notice
   for users.
4. Register with VERBİS if thresholds apply.

Until these are complete, run the pilot with **synthetic or anonymised
documents only**, or keep `OCR_PROVIDER=none` and process only e-invoice XML
(which never leaves the server: UBL parsing is local).

## 4. Technical measures (implemented)

| Requirement | Implementation |
|---|---|
| Tenant isolation | PostgreSQL RLS on every table + composite `(firm_id, id)` foreign keys + API role checks; non-members receive 404 |
| AuthN | Supabase Auth (e-mail verification, password reset); API verifies JWT signature/expiry/audience (HS256 or JWKS) |
| AuthZ | 5 firm roles + super admin; approvals/exports limited to owner/admin/accountant; DB triggers enforce approver role |
| Human approval | Journals are created as drafts; DB refuses approval unless balanced, ≥2 lines, period not locked |
| Secrets | Environment variables only; never in frontend bundle (only public `NEXT_PUBLIC_*` values) |
| Encryption in transit | HTTPS (TLS 1.2/1.3, HSTS) at nginx; TLS to Supabase/Anthropic |
| Encryption at rest | Supabase (AES-256 disk encryption); backups AES-256 encrypted |
| Files | Private buckets; short-lived signed URLs (default 300 s); magic-byte type validation; size limits; safe XML parsing (no XXE); image re-encoding strips metadata |
| Audit | Append-only `audit_logs` and `approvals` (UPDATE/DELETE blocked even for the DB owner) |
| Logs | JSON logs with identifiers only; no request bodies, amounts, names or document text |
| Web | CSP, X-Frame-Options DENY, nosniff, Permissions-Policy (camera self only); service worker never caches API data |
| Containers | Non-root users, read-only root filesystems, `no-new-privileges` |
| Rate limiting | nginx per-IP limits for API and uploads |
| Plan limits | Enforced by DB triggers |

## 5. Retention & deletion policy

| Data | Retention | Mechanism |
|---|---|---|
| Documents + extraction | `retention_until` = end of the 10th year after upload (TTK md. 82 = 10 yrs; VUK md. 253 = 5 yrs; configurable via `retention.document_years`) | `app.maintenance` daily |
| Soft-deleted, never-approved documents | 30 days | `app.maintenance` |
| Raw OCR text | 90 days (structured data kept) | `app.maintenance` |
| Finished processing jobs | 90 days | `app.maintenance` |
| Approved journal entries | kept with the taxpayer's books | detached from purged documents |
| Audit log | not purged by default (accountability) | — |
| Backups | 30 days rolling | `backup.sh` |
| Office termination | export data to the office, then delete firm (cascade) + storage prefix `<firm_id>/` within 30 days | manual super-admin procedure |

`app.maintenance` runs as a dry run until `RETENTION_EXECUTE=1` is set.
Data subject requests (access/erasure) are handled by the controller (office);
the operator assists within the processing agreement.

## 6. Incident response

1. Contain (suspend firm/users via super admin, rotate keys).
2. Assess scope using audit logs and request IDs.
3. Notify affected offices without delay; controllers notify the KVKK
   Authority within 72 hours where required.
4. Post-mortem and remediation record.

## 7. Known gaps before production

* Penetration test and dependency audit not yet performed.
* No WAF/DDoS layer beyond nginx rate limits (consider Cloudflare).
* MFA for office users: enable Supabase MFA (TOTP) and enforce for owners.

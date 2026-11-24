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

## Endpoints (Milestone 1)

### Health
| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/health` | — | Liveness: `{"status":"ok"}` |
| GET | `/health/ready` | — | Readiness: DB reachable; returns `schema_version`. 503 if not. |

### Current user
| Method | Path | Description |
|---|---|---|
| GET | `/me` | Profile + active firm memberships. Updates `last_seen_at`. |
| PATCH | `/me` | Body (all optional): `full_name`, `phone`, `locale` (`tr`/`en`). Unknown fields → 422. |

Response:
```json
{
  "id": "uuid", "email": "a@b.com", "full_name": "Ayşe Yılmaz", "phone": null,
  "locale": "tr", "is_super_admin": false,
  "memberships": [{"firm_id": "uuid", "firm_name": "Yıldız SMMM", "role": "owner"}]
}
```

### Firms (accounting offices)
| Method | Path | Roles | Description |
|---|---|---|---|
| POST | `/firms` | any authenticated user | Create firm. Caller becomes `owner`; trial subscription starts. Body: `name` (2–200), `tax_number` (10/11 digits, optional). → 201 |
| GET | `/firms` | member | Firms where caller is an active member, with `my_role`. |
| GET | `/firms/{firm_id}` | member | Details incl. `subscription` (plan, status, limits). |
| PATCH | `/firms/{firm_id}` | owner, admin | Body (optional): `name`, `tax_number`, `tax_office`, `email`, `phone`, `address`. Audit-logged. |
| GET | `/firms/{firm_id}/members` | member | Members with role and status. |
| GET | `/firms/{firm_id}/audit-logs` | owner, admin | Newest first. Query: `limit` (1–200, default 50), `before_id` (cursor). |

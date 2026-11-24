# Backup & Recovery

## What is backed up

| Data | Mechanism | Frequency | Encryption |
|---|---|---|---|
| Application database (schemas `public`, `app`) | `backup` service → `pg_dump --format=custom` | daily | AES-256 (OpenSSL, PBKDF2 200k iterations) with `BACKUP_PASSPHRASE` |
| Uploaded documents + Luca export files (local storage) | `backup` service → tar.gz | daily | same |
| Supabase Auth users | Supabase managed backups / PITR | Supabase plan | Supabase |
| Supabase Storage (if `STORAGE_BACKEND=supabase`) | Supabase managed backups; optionally `rclone sync` of the buckets | daily | Supabase |
| Self-hosted auth (`BACKUP_INCLUDE_AUTH_USERS=1`) | `pg_dump --data-only --table=auth.users` | daily | same |

Every file gets a `.sha256` checksum. Retention: `BACKUP_RETENTION_DAYS` (30).
Off-site copy: set `BACKUP_REMOTE_CMD` (e.g. rclone to S3-compatible storage in
a different provider/region). **Without an off-site copy, a lost VPS means lost
local backups.**

Targets: **RPO ≤ 24 h** (daily dumps; enable Supabase PITR for minutes),
**RTO ≤ 4 h**.

## Restore procedure

1. Provision a clean target database:
   * Supabase: a new project (auth schema present) or the original one after
     moving the broken schemas aside.
   * Local drill: `createdb` + `psql -f database/bootstrap/local_supabase_shim.sql`.
2. Copy the encrypted files and their `.sha256` next to `deploy/scripts/restore.sh`.
3. Run:
   ```bash
   export DATABASE_URL=postgresql://... BACKUP_PASSPHRASE=... STORAGE_DIR=/data/storage
   sh deploy/scripts/restore.sh maliscan-db-<ts>.dump.enc maliscan-files-<ts>.tar.gz.enc [maliscan-auth-<ts>.sql.enc]
   ```
   The script verifies checksums, decrypts to a private temp dir, restores the
   database (skipping the pre-existing `public` schema) and unpacks files.
4. `docker compose run --rm migrate` — must report `0 migration(s) applied`.
5. Verify: `/api/v1/health/ready`, log in, open a document, download an export.

## Restore drill — performed

A full drill was executed during development (2026-11-24) against the E2E
database:

| Check | Source | Restored |
|---|---|---|
| documents / journal lines / users / audit entries | 5 / 15 / 9 / 23 | 5 / 15 / 9 / 23 |
| RLS enabled on all tables, policy count | yes, 56 | yes, 56 |
| schema version | 0006 | 0006 |
| stored files | — | byte-identical (`diff -r`) |
| accounting triggers active | — | editing an approved entry's lines is rejected |

Repeat the drill **quarterly** and after every major upgrade; record the date
and result here.

## Key management

* `BACKUP_PASSPHRASE` is the only way to decrypt backups — store it in the
  office's password manager and in a sealed offline copy. Rotating it only
  affects new backups.
* `.env` is `chmod 600`, never committed, never sent to support channels.

# VPS Deployment Guide

Target: one Linux VPS (Ubuntu 24.04, ≥ 2 vCPU, 4 GB RAM, 40 GB disk) running
Docker, with Supabase (Auth + Postgres + Storage) as the managed backend.
Everything here was exercised in CI-like conditions (image builds, container
smoke tests, nginx config test, backup/restore drill); the full public
deployment itself must be performed and verified on your server.

## 1. Prerequisites

| Item | Notes |
|---|---|
| Domains | `app.example.com` (web) and `api.example.com` (API) → A/AAAA records to the VPS |
| Supabase project | Region **eu-central** (Frankfurt) recommended for latency/KVKK assessment |
| Anthropic API key | With a signed data processing agreement (see SECURITY_KVKK.md) |
| SMTP | Configure custom SMTP in Supabase Auth for verification/reset e-mails |

## 2. Supabase setup

1. **Auth → URL configuration**: Site URL `https://app.example.com`; redirect URLs
   `https://app.example.com/onboarding`, `https://app.example.com/reset-password`.
2. **Auth → Providers → Email**: enable "Confirm email".
3. **Storage**: create two **private** buckets: `documents`, `exports`. Do not add
   public policies — only the backend (service role) accesses them and hands out
   signed URLs.
4. **Database**: copy the direct connection string (port 5432) into `DATABASE_URL`.
5. **API keys**: `anon` key → `NEXT_PUBLIC_SUPABASE_ANON_KEY`; `service_role` key →
   `SUPABASE_SERVICE_ROLE_KEY` (server only); JWT secret → `SUPABASE_JWT_SECRET`
   (or leave empty when the project uses asymmetric JWT signing keys — the API
   then verifies tokens via JWKS from `SUPABASE_URL`).

## 3. Server preparation

```bash
sudo apt update && sudo apt install -y ca-certificates curl ufw fail2ban unattended-upgrades
curl -fsSL https://get.docker.com | sh
sudo ufw allow OpenSSH && sudo ufw allow 80 && sudo ufw allow 443 && sudo ufw enable
sudo adduser --disabled-password maliscan && sudo usermod -aG docker maliscan
```

Disable SSH password login (`PasswordAuthentication no`) and keep unattended
security upgrades enabled.

## 4. Application

```bash
sudo -iu maliscan
git clone <your-repo> && cd <repo>/maliscan
cp .env.example .env && chmod 600 .env    # fill in every value
openssl rand -hex 32                       # → SIGNING_SECRET
openssl rand -base64 48                    # → BACKUP_PASSPHRASE (store it in a password manager!)
docker compose build
docker compose run --rm migrate            # applies database/migrations (idempotent)
```

### TLS certificates (first time)

```bash
docker run --rm -p 80:80 -v maliscan_letsencrypt:/etc/letsencrypt certbot/certbot:v3.0.1 \
  certonly --standalone -d app.example.com --agree-tos -m ops@example.com --non-interactive
docker run --rm -p 80:80 -v maliscan_letsencrypt:/etc/letsencrypt certbot/certbot:v3.0.1 \
  certonly --standalone -d api.example.com --agree-tos -m ops@example.com --non-interactive
docker compose up -d
```

The `certbot` service renews automatically (webroot); reload nginx monthly via
cron: `docker compose exec nginx nginx -s reload`.

### Platform super admin

After registering your own account in the app:

```sql
UPDATE public.users SET is_super_admin = true WHERE email = 'you@example.com';
```

## 5. Verify

```bash
curl -s https://api.example.com/api/v1/health/ready     # {"status":"ready","schema_version":"0006"}
docker compose ps                                        # api, worker×2, web, nginx, certbot, backup, maintenance
docker compose logs -f worker                            # job.succeeded / job.failed events (IDs only)
```

Then in the browser: register → verify e-mail → create office → add taxpayer →
scan a receipt → review → approve → Luca export.

## 6. Updates

```bash
git pull && docker compose build && docker compose run --rm migrate && docker compose up -d
```

Migrations are append-only and checksum-protected; a modified migration aborts
the run.

## 7. Operations

| Task | Command |
|---|---|
| Logs | `docker compose logs --since 1h api worker` |
| Retry failed jobs | Super admin → İşler ve hatalar → "Tekrar dene" |
| Scale workers | `docker compose up -d --scale worker=4` |
| Retention dry run | `docker compose run --rm api python -m app.maintenance` |
| Manual backup | `docker compose exec backup sh /scripts/backup.sh` |
| API docs | disabled in production; run with `ENVIRONMENT=staging` to inspect `/api/v1/docs` |

## 8. Behind a TLS-inspecting corporate proxy

Builds accept an optional CA: `docker compose build` with
`DOCKER_BUILDKIT=1` and `--secret id=ca_bundle,src=/path/ca.crt` (see the
Dockerfiles). Not needed on a normal VPS.

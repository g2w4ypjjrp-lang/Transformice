#!/bin/sh
# Restore an encrypted MaliScan backup.
#   restore.sh <db.dump.enc> [files.tar.gz.enc] [auth.sql.enc]
# Env: DATABASE_URL (target, should be an EMPTY database with the Supabase
#      auth schema present, or a fresh DB prepared with --with-shim locally),
#      BACKUP_PASSPHRASE, STORAGE_DIR (default /data/storage).
set -eu
: "${BACKUP_PASSPHRASE:?BACKUP_PASSPHRASE is required}"
DB_URL=$(echo "${DATABASE_URL:?DATABASE_URL is required}" | sed 's#postgresql+psycopg://#postgresql://#')
DB_FILE=${1:?usage: restore.sh <db.dump.enc> [files.tar.gz.enc]}
FILES_FILE=${2:-}
AUTH_FILE=${3:-}
dec() { openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass env:BACKUP_PASSPHRASE -in "$1"; }

[ -f "$DB_FILE.sha256" ] && sha256sum -c "$DB_FILE.sha256"
if [ -n "$AUTH_FILE" ]; then
  # auth users first: public.users references auth.users
  dec "$AUTH_FILE" | psql -v ON_ERROR_STOP=1 -q "$DB_URL"
fi
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
umask 077
dec "$DB_FILE" > "$TMP/db.dump"
# The target already has the public schema (Supabase / fresh PostgreSQL): skip recreating it.
pg_restore --list "$TMP/db.dump" | grep -v -E ' SCHEMA - public ' > "$TMP/toc.list"
pg_restore --no-owner --no-privileges --exit-on-error --use-list "$TMP/toc.list" --dbname "$DB_URL" "$TMP/db.dump"
if [ -n "$FILES_FILE" ]; then
  [ -f "$FILES_FILE.sha256" ] && sha256sum -c "$FILES_FILE.sha256"
  mkdir -p "${STORAGE_DIR:-/data/storage}"
  dec "$FILES_FILE" | tar -C "${STORAGE_DIR:-/data/storage}" -xzf -
fi
echo "restore ok"

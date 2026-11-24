#!/bin/sh
# MaliScan backup: encrypted PostgreSQL dump + encrypted file storage archive.
#
# Env: DATABASE_URL (postgresql://...), BACKUP_PASSPHRASE (required),
#      BACKUP_DIR (default /backups), STORAGE_DIR (default /data/storage),
#      BACKUP_RETENTION_DAYS (default 30), BACKUP_INCLUDE_AUTH_USERS=1 (self-hosted
#      auth only; on Supabase, auth is covered by Supabase's own backups/PITR),
#      BACKUP_REMOTE_CMD (optional, e.g.
#      "rclone copy {file} remote:maliscan-backups") run for each new file.
set -eu
: "${BACKUP_PASSPHRASE:?BACKUP_PASSPHRASE is required}"
DB_URL=$(echo "${DATABASE_URL:?DATABASE_URL is required}" | sed 's#postgresql+psycopg://#postgresql://#')
DIR=${BACKUP_DIR:-/backups}
STORAGE=${STORAGE_DIR:-/data/storage}
KEEP=${BACKUP_RETENTION_DAYS:-30}
TS=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$DIR"
umask 077

enc() { openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass env:BACKUP_PASSPHRASE -out "$1"; }

DB_FILE="$DIR/maliscan-db-$TS.dump.enc"
pg_dump --format=custom --no-owner --no-privileges --schema=public --schema=app "$DB_URL" | enc "$DB_FILE"
AUTH_FILE="$DIR/maliscan-auth-$TS.sql.enc"
if [ "${BACKUP_INCLUDE_AUTH_USERS:-0}" = "1" ]; then
  pg_dump --data-only --table=auth.users "$DB_URL" | enc "$AUTH_FILE"
fi
FILES_FILE="$DIR/maliscan-files-$TS.tar.gz.enc"
if [ -d "$STORAGE" ]; then
  tar -C "$STORAGE" -czf - . | enc "$FILES_FILE"
fi
for f in "$DB_FILE" "$AUTH_FILE" "$FILES_FILE"; do
  [ -f "$f" ] || continue
  sha256sum "$f" > "$f.sha256"
  if [ -n "${BACKUP_REMOTE_CMD:-}" ]; then
    sh -c "$(echo "$BACKUP_REMOTE_CMD" | sed "s#{file}#$f#g")"
  fi
done
find "$DIR" -name 'maliscan-*' -mtime +"$KEEP" -delete
echo "backup ok $TS"

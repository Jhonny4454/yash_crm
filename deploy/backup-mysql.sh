#!/usr/bin/env bash
#
# backup-mysql.sh — nightly mysqldump, kept 14 days.
#
# The app has its own nightly_backup job, but that runs inside the application
# and depends on it being healthy. This is a database-level dump that works
# even when the app does not, which is exactly when you need it.
#
#   0 3 * * *  root  /opt/yash-crm/deploy/backup-mysql.sh
#
set -euo pipefail

DEST=/opt/yash-crm/backups
KEEP_DAYS=14
STAMP=$(date +%Y%m%d-%H%M%S)

source /opt/yash-crm/.env
# Pull credentials out of DATABASE_URL rather than duplicating them.
proto_removed=${DATABASE_URL#*://}
creds=${proto_removed%%@*}
DB_USER=${creds%%:*}
DB_PASS=${creds#*:}
hostpart=${proto_removed#*@}
DB_NAME=${hostpart#*/}
DB_NAME=${DB_NAME%%\?*}

mkdir -p "$DEST"
OUT="$DEST/${DB_NAME}-${STAMP}.sql.gz"

MYSQL_PWD="$DB_PASS" mysqldump \
    --user="$DB_USER" --host=127.0.0.1 \
    --single-transaction --quick --routines --triggers \
    --default-character-set=utf8mb4 \
    "$DB_NAME" | gzip -9 > "$OUT"

chmod 600 "$OUT"
find "$DEST" -name "${DB_NAME}-*.sql.gz" -mtime +$KEEP_DAYS -delete

# A backup you have never restored is a hope, not a backup. This at least
# proves the file is a readable gzip with content in it.
gzip -t "$OUT" && [[ $(stat -c%s "$OUT") -gt 1024 ]] \
    && echo "ok  $OUT ($(du -h "$OUT" | cut -f1))" \
    || { echo "BACKUP LOOKS WRONG: $OUT"; exit 1; }

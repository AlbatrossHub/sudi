#!/bin/bash
# Restore a raw production dump as the ETL SOURCE database (sudi_src19), neutralize it,
# and stage its filestore. Re-runnable: drops and rebuilds the source every time —
# exactly what the final cutover does with a fresh dump.
#
#   [ETL_SRC=<source db>] tools/etl/restore_src.sh [dump] [filestore_dir]
#   default dump:      ~/prod-backups/sudi-2026-09-28/sudi.dump
#   default filestore: ~/prod-backups/sudi-2026-09-28/filestore
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
DB=${ETL_SRC:-sudi_src19}   # e.g. ETL_SRC=sudi_local_src19 for the local dev copy
ETL_DIR=/opt/odoo20/etl
DUMP=${1:-$HOME/prod-backups/sudi-2026-09-28/sudi.dump}
FS=${2:-$HOME/prod-backups/sudi-2026-09-28/filestore}

[ -f "$DUMP" ] || { echo "dump not found: $DUMP" >&2; exit 1; }
[ -d "$FS" ]   || { echo "filestore dir not found: $FS" >&2; exit 1; }

echo "[$(date +%T)] restoring $DUMP -> $DB"
dropdb --if-exists "$DB"
createdb "$DB"
# --no-owner: prod role names do not exist locally; om1203 owns everything here.
pg_restore --no-owner --role="$(whoami)" -d "$DB" "$DUMP"

echo "[$(date +%T)] neutralizing (crons, mail, owa sidecar, base url)"
psql -q -d "$DB" -v ON_ERROR_STOP=1 -f "$HERE/neutralize_src.sql"

echo "[$(date +%T)] staging filestore -> $ETL_DIR/data19/filestore/$DB (hardlinks)"
mkdir -p "$ETL_DIR/data19/filestore"
rm -rf "$ETL_DIR/data19/filestore/$DB"
# hardlink copy: instant and space-free; falls back to a real copy across filesystems.
cp -al "$FS" "$ETL_DIR/data19/filestore/$DB" 2>/dev/null || cp -a "$FS" "$ETL_DIR/data19/filestore/$DB"

echo "[$(date +%T)] summary:"
psql -d "$DB" -Atc "
SELECT 'installed modules: '||count(*) FROM ir_module_module WHERE state='installed';
SELECT 'users:             '||count(*) FROM res_users;
SELECT 'mail_message:      '||count(*) FROM mail_message;
SELECT 'ir_attachment:     '||count(*) FROM ir_attachment;
SELECT 'active crons:      '||count(*) FROM ir_cron WHERE active;
SELECT 'active mail srv:   '||count(*) FROM ir_mail_server WHERE active;
SELECT 'owa enabled:       '||count(*) FROM owa_account WHERE enabled;"
echo "filestore files:   $(find "$ETL_DIR/data19/filestore/$DB" -type f | wc -l)"
echo "[$(date +%T)] done. Source ready: $DB"

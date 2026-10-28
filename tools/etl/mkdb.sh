#!/bin/bash
# Create a fresh Odoo 20 TARGET database for the ETL: base, then the company set to India
# (so the l10n_in chart loads), then the frozen production module list. Run as om1203:
#   tools/etl/mkdb.sh sudi20_etl_dry1            # full 142-module target
#   tools/etl/mkdb.sh sudi20_etl_smoke --base-only  # 1-minute smoke target (base only)
#   ETL_SRC=sudi_local_src19 tools/etl/mkdb.sh sudi20_local   # module list of another source
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
DB=${1:?database name}
MODE=${2:-full}
PY=/opt/odoo20/odoo20-venv/bin/python
ODOO=/opt/odoo20/odoo20/odoo-bin
CONF=$HERE/conf/odoo20-etl.conf
LOG=/opt/odoo20/etl/logs/mkdb_${DB}.log
COMMON="-c $CONF -d $DB --workers=0 --max-cron-threads=0 --http-port=8122 --logfile=$LOG"

echo "[$(date +%T)] creating $DB (log: $LOG)"
dropdb --if-exists "$DB"; createdb "$DB"
$PY $ODOO $COMMON -i base --stop-after-init

echo "[$(date +%T)] base installed; configuring company"
$PY $ODOO shell $COMMON --no-http <<'PYEOF'
c = env.company
c.write({
    'name': 'SUPER DIAMOND PROCESSORS PRIVATE LIMITED',
    'country_id': env.ref('base.in').id,
    'state_id': env.ref('base.state_in_mh').id,
    'currency_id': env.ref('base.INR').id,
    'vat': '27AAWCS0027J1Z0',
    'street': 'ROOM NO 110, B-WING, Maa Ashapura CHS',
    'street2': 'Nanasaheb Dharmadhikari Road, Mumbai',
    'city': 'Mumbai Suburban', 'zip': '400051',
    'phone': '+91 80808 09288',
})
env.ref('base.INR').write({'active': True})
env.cr.commit()
print("company:", c.name, c.country_id.code, c.state_id.code, c.currency_id.name)
PYEOF

if [ "$MODE" != "--base-only" ]; then
    SRC=${ETL_SRC:-sudi_src19}
    TAG=$([ "$SRC" = sudi_src19 ] && echo "" || echo "_$SRC")
    MODS=$(grep -v '^#' "$HERE/target_modules_prod$TAG.txt" | grep -v '^$' | paste -sd,)
    N=$(echo "$MODS" | tr ',' '\n' | wc -l)
    echo "[$(date +%T)] installing $N modules"
    $PY $ODOO $COMMON -i "$MODS" --stop-after-init
fi

# a migration target must never run automation on its own: every cron off, outgoing mail off.
# (production: re-enable deliberately at go-live, see docs/etl_cutover_checklist.md)
psql -d "$DB" -qc "UPDATE ir_cron SET active = false; UPDATE ir_mail_server SET active = false;"

echo "[$(date +%T)] done; sanity:"
psql -d "$DB" -Atc "SELECT 'installed: '||count(*) FROM ir_module_module WHERE state='installed';
SELECT 'taxes: '||count(*) FROM account_tax; SELECT 'accounts: '||count(*) FROM account_account" 2>/dev/null \
  || psql -d "$DB" -Atc "SELECT 'installed: '||count(*) FROM ir_module_module WHERE state='installed'"
{ grep -cE " (ERROR|CRITICAL) " "$LOG" || true; } | sed 's/^/errors in log: /'

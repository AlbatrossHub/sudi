"""Static configuration of the sudi 19→20 ETL. Everything run-specific comes from run.py's CLI.

Engine and layout inherited from the marut v18→20 ETL
(/home/om1203/opt/odoo20/custom/marut_20/tools/etl) — see docs/etl_runbook.md chapter 0.
"""
import os

SRC_DB = os.environ.get("ETL_SRC", "sudi_src19")     # Odoo 19 staging copy (same PG cluster)
SRC_SCHEMA = "src19"                                  # foreign schema exposing SRC_DB inside the target DB
# per-source artifact names: the prod source keeps the historical names (target_modules_prod.txt,
# checksums_src.json); any other source (e.g. the local dev copy sudi_local_src19) gets its own files.
SRC_TAG = "" if SRC_DB == "sudi_src19" else "_" + SRC_DB
TARGET_MODULES_FILE = os.path.join(os.path.dirname(__file__), f"target_modules_prod{SRC_TAG}.txt")
OFFSET = 10_000_000                                   # SQL-lane id offset (old_id + OFFSET)
ODOO_CONF = "/opt/odoo20/custom/sudi_20/tools/etl/conf/odoo20-etl.conf"
ODOO_ROOT = "/opt/odoo20/odoo20"
ETL_DIR = "/opt/odoo20/etl"
LOG_DIR = f"{ETL_DIR}/logs"
ARCHIVE_DIR = f"{ETL_DIR}/archive"
BASELINE_DIR = f"{ETL_DIR}/baseline"
BASELINE_FILE = f"{BASELINE_DIR}/checksums_src{SRC_TAG}.json"
FILESTORE_SRC = f"{ETL_DIR}/data19/filestore/{SRC_DB}"  # v19 filestore staged by restore_src.sh
COMPANY = 1                                           # the single company on 19 (SUPER DIAMOND PROCESSORS)
ORM_BATCH = 500

# Ports reserved for the ETL (8069 is the live v19 server and is NEVER used here;
# 8098/8099 belong to the user's manual sudi20_test QA server):
PORT_BASELINE19 = 8119   # read-only v19 server for page fetch/screenshots
PORT_SHELL20 = 8121      # bootstrap.open_env / odoo shell against targets
PORT_MKDB = 8122         # module installs while building a target DB
PORT_TARGET20 = 8123     # v20 server on the ETL target for P4/P5 validation

# modules ADDED on 20 beyond prod parity (documented exceptions, see etl_mapping_rules.md):
# mail_tracking: v20 split mail.tracking.value out of mail; prod has 6,984 tracked values.
EXTRA_MODULES = ("mail_tracking",)

# v19→20 module merges (verified 2026-09-28 against /opt/odoo20 sources):
# these 5 names exist on the 19 DB but their code was absorbed by other v20 modules.
MERGED_MODULES = {
    "base_vat": "account",        # VIES/VAT checks live in base+account on 20
    "account_add_gln": "account",
    "hr_org_chart": "hr",
    "hr_homeworking": "hr",
    "hr_hourly_cost": "hr",
}

# Legacy IANA aliases a v19 DB may hold in tz columns; v20's tz selection lists only canonical
# names and the ORM refuses the alias (the local dev DB has 154 partners on Asia/Calcutta).
TZ_ALIASES = {
    "Asia/Calcutta": "Asia/Kolkata", "Asia/Katmandu": "Asia/Kathmandu", "Asia/Saigon": "Asia/Ho_Chi_Minh",
    "Asia/Rangoon": "Asia/Yangon", "Asia/Dacca": "Asia/Dhaka", "Asia/Thimbu": "Asia/Thimphu",
    "Europe/Kiev": "Europe/Kyiv", "America/Buenos_Aires": "America/Argentina/Buenos_Aires",
    "US/Eastern": "America/New_York", "US/Central": "America/Chicago", "US/Pacific": "America/Los_Angeles",
}

# context that keeps the 20 ORM quiet while loading history
SILENT_CTX = dict(
    tracking_disable=True, mail_create_nolog=True, mail_create_nosubscribe=True, mail_notrack=True,
    mail_activity_automation_skip=True, mail_auto_subscribe_no_notify=True, active_test=False,
)

# columns never copied by the ORM lane
IGNORED_COLUMNS = {"id", "create_date", "write_date", "create_uid", "write_uid", "__last_update", "display_name",
                   "message_main_attachment_id", "website_message_ids", "message_ids", "activity_ids",
                   "message_follower_ids", "rating_ids", "access_token", "access_url"}
# sudi never went through the v13 migration marut did — no legacy id columns.
LEGACY_PREFIXES = ()

# A run is a *production* run only when explicitly asked (run.py --production). Every other run is a dry run:
# the engine blanks the WhatsApp connector credentials (owa_account + sidecar url param), deactivates crons
# and outgoing mail servers on the target after each phase so that nothing loaded from 19 can ever reach a
# customer (or the live sidecar) from a test database.
PRODUCTION = os.environ.get("ETL_PRODUCTION") == "1"

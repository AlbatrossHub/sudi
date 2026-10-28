-- Neutralize the freshly restored SOURCE snapshot (sudi_src19) so nothing in it can ever
-- reach a customer, the live sidecar, or Meta cloud. Run by restore_src.sh; idempotent.
-- The same rules are re-applied to every TARGET db after each ETL phase by the engine's
-- dry_run_guard (core.py) — this file is the source-side twin.

UPDATE ir_cron SET active = false WHERE active;
UPDATE ir_mail_server SET active = false WHERE active;
UPDATE fetchmail_server SET active = false WHERE active;

-- WhatsApp connector: kill the bridge to the Node sidecar and Meta cloud.
UPDATE owa_account SET
    enabled = false,
    is_listening = false,
    sidecar_url = 'http://dry-run-disabled',
    sidecar_api_key = 'dry-run-disabled',
    webhook_secret = 'dry-run-disabled',
    cloud_app_secret = NULL,
    cloud_access_token = NULL,
    cloud_verify_token = NULL,
    qr_code_base64 = NULL;
UPDATE ir_config_parameter SET value = 'http://dry-run-disabled'
 WHERE key = 'open_whatsapp_connector.default_sidecar_url';

-- The baseline server renders this DB on port 8119; freeze the base url so nothing
-- rewrites it back to the production domain.
UPDATE ir_config_parameter SET value = 'http://localhost:8119' WHERE key = 'web.base.url';
INSERT INTO ir_config_parameter (key, value, create_date, write_date, create_uid, write_uid)
SELECT 'web.base.url.freeze', 'True', now(), now(), 1, 1
 WHERE NOT EXISTS (SELECT 1 FROM ir_config_parameter WHERE key = 'web.base.url.freeze');

-- Enterprise expiration nag must never block the read-only baseline UI.
DELETE FROM ir_config_parameter WHERE key IN ('database.expiration_date', 'database.expiration_reason');

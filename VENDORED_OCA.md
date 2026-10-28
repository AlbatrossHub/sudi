# Vendored OCA addons (temporary)

`fastapi/` (OCA/rest-framework, 19.0.1.1.0) and `endpoint_route_handler/`
(OCA/web-api, 19.0.1.2.0) are vendored here because OCA has not released
20.0 versions yet (checked PyPI 2026-09-28). They were copied from the v19
venv site-packages and ported to Odoo 20 in-house.

When OCA publishes 20.0 releases: pip-install them into the v20 venv,
delete these two directories, and diff our port against theirs first.

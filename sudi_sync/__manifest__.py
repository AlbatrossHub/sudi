{
    "name": "Sudi Offline Sync",
    "version": "19.0.1.0.0",
    "summary": "Change log, device registry, idempotency and upload staging for the Sudi mobile apps",
    "description": """
The offline machinery behind the Sudi field and customer apps.

Deliberately holds no HTTP code: the change log, the cursor, the device
registry, the idempotency keys and the worklist payloads are ORM concerns with
real invariants, and keeping them here means they are testable without a client
and reusable by anything other than FastAPI. The HTTP surface lives in
sudi_api.

See docs/MOBILE_API_PLAN.md sections 5.4, 6 and 7.
""",
    "category": "Inventory/Inventory",
    "author": "Sudi",
    "license": "LGPL-3",
    "depends": ["diamond"],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_config_parameter.xml",
        "data/ir_cron.xml",
        "views/sudi_api_device_views.xml",
        "views/sudi_sync_change_views.xml",
        "views/sudi_upload_views.xml",
        "views/menus.xml",
    ],
    "installable": True,
}

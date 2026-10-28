{
    "name": "Sudi Offline Sync",
    "version": "20.0.1.1.0",
    "summary": "Change log, device registry, idempotency and upload staging for the Sudi mobile apps",
    "description": """
The offline machinery behind the Sudi field and customer apps, and the
in-app notices the staff app shows on its bell.

Deliberately holds no HTTP code: the change log, the cursor, the device
registry, the idempotency keys and the worklist payloads are ORM concerns with
real invariants, and keeping them here means they are testable without a client
and reusable by anything other than FastAPI. The HTTP surface lives in
sudi_api.

See docs/MOBILE_API_PLAN.md sections 5.5, 6 and 7, and
docs/MOBILE_V2_PERSONAS_PLAN.md section 4.
""",
    "category": "Inventory/Inventory",
    "author": "Sudi",
    "license": "LGPL-3",
    "depends": ["diamond"],
    "data": [
        "data/ir_config_parameter.xml",
        "data/ir_cron.xml",
        "views/sudi_api_device_views.xml",
        "views/sudi_sync_change_views.xml",
        "views/sudi_upload_views.xml",
        "views/sudi_app_notice_views.xml",
        "views/menus.xml",
        'security/ir.access.csv',
    ],
    "installable": True,
}

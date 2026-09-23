{
    "name": "Sudi Mobile API",
    "version": "19.0.1.0.0",
    "summary": "FastAPI surface for the Sudi field and customer apps",
    "description": """
The HTTP surface over sudi_sync.

Two endpoints from one code base: /api/field/v1 for pickup, delivery and
job-work staff, and /api/customer/v1 for customers. Same JWT helpers, same
error envelope, same sync machinery, but separate OpenAPI documents and
separate blast radius, so the staff app's operations never appear in the
customer contract and either surface can be disabled without the other.

See docs/MOBILE_API_PLAN.md sections 3 and 5.
""",
    "category": "Inventory/Inventory",
    "author": "Sudi",
    "license": "LGPL-3",
    "depends": ["sudi_sync", "fastapi"],
    "data": [
        "data/ir_config_parameter.xml",
        "data/fastapi_endpoint.xml",
    ],
    "external_dependencies": {
        # Distribution name, resolved by importlib.metadata.version()
        "python": ["PyJWT"],
    },
    "post_init_hook": "post_init_hook",
    "installable": True,
}

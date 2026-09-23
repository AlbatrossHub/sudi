{
    "name": "Sudi Diamond Billing Review",
    "version": "19.0.1.0.0",
    "summary": "Review delivered diamond job work and settle it by invoice or reference statement",
    "description": """
Billing Review screen for Sudi diamond job work.

Lists every delivered job-work order with what it contained (pieces, carats,
job types, receipt-wise rates), whether it has been billed, and lets the
billing reviewer settle any selection into draft invoices or off-book
reference statements, one per customer. Includes the developer-mode Reference
Ledger.
""",
    "category": "Inventory/Inventory",
    "author": "Sudi",
    "license": "LGPL-3",
    "depends": ["diamond"],
    "data": [
        "security/ir.model.access.csv",
        "views/reference_statement_views.xml",
        "views/billing_review_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "sudi_diamond_billing/static/src/js/billing_review.js",
            "sudi_diamond_billing/static/src/xml/billing_review.xml",
            "sudi_diamond_billing/static/src/scss/billing_review.scss",
        ],
    },
    "installable": True,
    "application": True,
}

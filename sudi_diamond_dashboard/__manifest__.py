{
    "name": "Sudi Diamond Job Work Analytics",
    "version": "19.0.1.0.0",
    "summary": "Neumorphic analytical dashboard for diamond job work",
    "description": """
Analytical dashboard for Sudi diamond job work.

Job type, customer and quantity statistics against the day, week, month, year
or a custom date range, with a Pieces/Carats toggle. Restricted to Inventory
Administrators.
""",
    "category": "Inventory/Inventory",
    "author": "Sudi",
    "license": "LGPL-3",
    "depends": [
        "diamond",
    ],
    "data": [
        "views/dashboard_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "sudi_diamond_dashboard/static/src/js/dashboard/trend_chart.js",
            "sudi_diamond_dashboard/static/src/js/dashboard/dashboard.js",
            "sudi_diamond_dashboard/static/src/xml/dashboard.xml",
            "sudi_diamond_dashboard/static/src/scss/dashboard.scss",
        ],
    },
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": True,
}

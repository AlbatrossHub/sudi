# -*- coding: utf-8 -*-
{
    'name': 'Dynamic PWA Branding',
    'version': '19.0.1.0.0',
    'category': 'Website',
    'summary': 'Dynamic Progressive Web App Branding & AI-Powered Customization',
    'description': """
Dynamic PWA Branding Module
===========================

This module extends Odoo's PWA (Progressive Web App) capabilities with full dynamic customization:

✨ Features
-----------
* **Dynamic App Name** - Customize your PWA app name
* **Custom App Icons** - Upload 192x192 and 512x512 icons
* **Theme Colors** - Set theme and background colors dynamically
* **Splash Screen** - Custom splash/loading screen branding
* **iOS Support** - Apple touch icons and splash screens
* **Customer Portal PWA** - Separate PWA settings for customer-facing portal
* **AI Branding Generator** - Auto-generate PWA branding with AI prompts 🎨🤖
* **Live Preview** - See your PWA branding in real-time
* **Multi-Company Support** - Different PWA branding per company

🚀 Installation
---------------
1. Install this module
2. Go to Settings > General Settings > Progressive Web App
3. Customize your PWA branding
4. Generate AI branding suggestions (optional)

📱 PWA Installation
-------------------
Users can install your branded PWA from any modern browser by clicking
"Install" or "Add to Home Screen" in their browser menu.

🎨 AI Branding
--------------
Use the built-in AI prompt templates to generate:
- Color schemes based on your brand
- Icon suggestions
- Marketing copy for app descriptions

    """,
    'author': 'Aura Odoo Tech',
    'website': 'https://www.auraodoo.tech',
   
    'license': 'LGPL-3',
    'depends': [
        'base',
        'web',
        'base_setup',
    ],
    'data': [
        'security/pwa_security.xml',
        'security/ir.model.access.csv',
        'data/pwa_default_data.xml',
        'views/pwa_config_views.xml',
        'views/res_config_settings_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'pwa_dynamic_branding/static/src/css/pwa_settings.css',
            'pwa_dynamic_branding/static/src/js/pwa_preview.js',
        ],
    },
    'images': ['static/description/banner.png'],
    'installable': True,
    'application': False,
    'auto_install': False,
}

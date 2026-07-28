# -*- coding: utf-8 -*-
{
    'name': 'Web WhatsApp OTP Login',
    'version': '19.0.1.0',
    'category': 'Website/Website',
    'summary': 'Passwordless login using phone number and WhatsApp OTP',
    'description': """
        This module introduces passwordless authentication for Odoo e-commerce websites.
        Users can log in with their phone number via a dynamic One-Time Password (OTP)
        sent directly to their WhatsApp.
    """,
    'author': 'Albatross',
    'depends': [
        'base',
        'web',
        'portal',
        'website',
        'open_whatsapp_connector',
    ],
    'data': [
        'data/otp_config_data.xml',
        'views/web_auth_otp_templates.xml',
    ],
    'assets': {
        'web.assets_frontend': [
            'web_auth_otp_login/static/src/css/otp_login.css',
            'web_auth_otp_login/static/src/js/otp_login.js',
        ],
    },
    'images': ['static/description/banner.png'],
    'module_type': 'official',
    'installable': True,
    'application': False,
    'support': 'contact.albatrosswork@gmail.com',
    'price': 9.99,
    'currency': 'EUR',
    'license': 'OPL-1',
}
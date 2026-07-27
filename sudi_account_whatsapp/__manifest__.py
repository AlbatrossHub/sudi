# -*- coding: utf-8 -*-
{
    'name': 'Sudi Account WhatsApp Integration',
    'summary': 'Send invoices directly via WhatsApp from account.move form view',
    'version': '19.0.1.0.0',
    'category': 'Accounting/Accounting',
    'author': 'Sudi',
    'license': 'LGPL-3',
    'depends': [
        'account',
        'open_whatsapp_connector',
    ],
    'data': [
        'views/account_move_views.xml',
    ],
    'installable': True,
    'application': False,
}

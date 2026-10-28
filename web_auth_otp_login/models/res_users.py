# -*- coding: utf-8 -*-
from odoo import models
from odoo.exceptions import AccessDenied
from odoo.http import request
from odoo.tools import consteq

# Single-use login nonce, placed in the HTTP session by the OTP controllers
# right after a WhatsApp OTP was verified, and consumed here exactly once.
OTP_LOGIN_NONCE_KEY = 'otp_login_nonce'
OTP_LOGIN_NONCE_LOGIN_KEY = 'otp_login_nonce_login'


class ResUsers(models.Model):
    _inherit = 'res.users'

    def _check_credentials(self, credential, user_agent_env):
        """Accept the ``whatsapp_otp`` credential type.

        Odoo 20 port: the controllers used to write session.uid/session_token
        by hand, bypassing ``res.users.authenticate`` (and with it login
        throttling, login logging and the MFA flow). They now go through
        ``odoo.http.session.authenticate`` with this credential type instead.
        """
        if credential.get('type') != 'whatsapp_otp':
            return super()._check_credentials(credential, user_agent_env)

        session = request.session if request else None
        token = credential.get('token') or ''
        nonce = session.pop(OTP_LOGIN_NONCE_KEY, None) if session else None
        nonce_login = session.pop(OTP_LOGIN_NONCE_LOGIN_KEY, None) if session else None
        if not (
            token
            and nonce
            and nonce_login == credential.get('login')
            and consteq(nonce, token)
        ):
            raise AccessDenied()
        return {
            'uid': self.env.user.id,
            'auth_method': 'whatsapp_otp',
            'mfa': 'default',
        }

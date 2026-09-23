"""One-time passwords for customer login and signup.

Ported from ``investo_be/auth_custom``, narrowed to the one channel this
project has (WhatsApp, decision D4) and moved out of the HTTP session.

The web flow in ``web_auth_otp_login`` keeps the **plaintext** code in
``request.session``. That cannot serve a stateless API, and it should not serve
the web either, so this model is the shared home for both: the code is never
stored, only a salted PBKDF2 hash; comparison is constant time; every code is
single use; and both issuing and verifying are rate limited, because either one
is an oracle if left unbounded.
"""

import hashlib
import hmac
import logging
import secrets
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

CHANNEL_WHATSAPP = "whatsapp"

PURPOSE_LOGIN = "login"
PURPOSE_REGISTER = "register"

PARAM_LENGTH = "sudi_auth.otp_length"
PARAM_TTL = "sudi_auth.otp_ttl_minutes"
PARAM_MAX_ATTEMPTS = "sudi_auth.otp_max_attempts"
PARAM_COOLDOWN = "sudi_auth.otp_resend_cooldown_seconds"
PARAM_MAX_PER_HOUR = "sudi_auth.otp_max_per_hour"
PARAM_RETENTION_DAYS = "sudi_auth.otp_retention_days"
PARAM_LOG_CODES = "sudi_auth.otp_log_codes"

# The body template is shared with web_auth_otp_login on purpose: a customer
# who uses both the PWA and the app should get the same message.
PARAM_BODY_TEMPLATE = "web_auth_otp_login.message_body_template"

DEFAULTS = {
    PARAM_LENGTH: 6,
    PARAM_TTL: 5,
    PARAM_MAX_ATTEMPTS: 5,
    PARAM_COOLDOWN: 60,
    PARAM_MAX_PER_HOUR: 5,
    PARAM_RETENTION_DAYS: 30,
}

# A 6-digit code is low entropy by nature, so this is defence in depth against
# database disclosure rather than a real work factor; the short TTL is what
# actually bounds an attacker.
_PBKDF2_ITERATIONS = 100_000


class SudiOtpError(UserError):
    """An OTP failure that is safe to show a customer."""

    sudi_code = "OTP_INVALID"


class SudiOtpNotFound(SudiOtpError):
    sudi_code = "OTP_NOT_FOUND"


class SudiOtpExpired(SudiOtpError):
    sudi_code = "OTP_EXPIRED"


class SudiOtpRateLimited(SudiOtpError):
    sudi_code = "OTP_RATE_LIMITED"


class SudiOtpUndeliverable(SudiOtpError):
    """The code was never sent, so telling the customer to wait would be a lie."""

    sudi_code = "OTP_UNDELIVERABLE"


class SudiAuthOtp(models.Model):
    _name = "sudi.auth.otp"
    _description = "Sudi One-Time Password"
    _inherit = ["sudi.diamond.whatsapp.mixin"]
    _order = "id desc"
    _rec_name = "identifier"

    channel = fields.Selection(
        [(CHANNEL_WHATSAPP, "WhatsApp")],
        required=True,
        default=CHANNEL_WHATSAPP,
        readonly=True,
    )
    identifier = fields.Char(
        required=True,
        index=True,
        readonly=True,
        help="The normalised ten-digit phone number the code was sent to.",
    )
    purpose = fields.Selection(
        [(PURPOSE_LOGIN, "Login"), (PURPOSE_REGISTER, "Registration")],
        required=True,
        readonly=True,
    )
    # Never the code itself. Restricted because they are still secrets.
    code_hash = fields.Char(required=True, readonly=True, groups="base.group_system")
    salt = fields.Char(required=True, readonly=True, groups="base.group_system")
    expires_at = fields.Datetime(required=True, index=True, readonly=True)
    attempts = fields.Integer(default=0, readonly=True)
    consumed_at = fields.Datetime(readonly=True)
    locked = fields.Boolean(
        default=False,
        readonly=True,
        help="Set after too many wrong guesses. A new code must be requested.",
    )
    partner_id = fields.Many2one(
        "res.partner", ondelete="set null", index=True, readonly=True
    )
    delivery_state = fields.Selection(
        [("pending", "Pending"), ("sent", "Sent"), ("failed", "Failed")],
        default="pending",
        readonly=True,
        help="Whether the channel accepted the message. Support needs to be "
             "able to see that a code failed to send rather than guess it.",
    )
    delivery_error = fields.Char(readonly=True)

    # ------------------------------------------------------------------
    # configuration
    # ------------------------------------------------------------------
    @api.model
    def _sudi_param(self, key):
        default = DEFAULTS[key]
        try:
            value = int(
                self.env["ir.config_parameter"].sudo().get_param(key, default)
            )
        except (TypeError, ValueError):
            return default
        return value if value > 0 else default

    # ------------------------------------------------------------------
    # hashing
    # ------------------------------------------------------------------
    @api.model
    def _sudi_hash_code(self, code, salt):
        return hashlib.pbkdf2_hmac(
            "sha256", (code or "").encode(), (salt or "").encode(),
            _PBKDF2_ITERATIONS,
        ).hex()

    @api.model
    def _sudi_generate_code(self):
        length = self._sudi_param(PARAM_LENGTH)
        # secrets, not random: the web flow used random.randint, which is
        # seeded from the clock and predictable.
        return "".join(str(secrets.randbelow(10)) for _ in range(length))

    # ------------------------------------------------------------------
    # issuing
    # ------------------------------------------------------------------
    @api.model
    def _sudi_assert_can_issue(self, identifier, purpose):
        now = fields.Datetime.now()
        recent = self.sudo().search(
            [
                ("identifier", "=", identifier),
                ("create_date", ">=", now - timedelta(hours=1)),
            ]
        )
        if len(recent) >= self._sudi_param(PARAM_MAX_PER_HOUR):
            raise SudiOtpRateLimited(_(
                "Too many codes requested for this number. Try again later."
            ))
        cooldown = self._sudi_param(PARAM_COOLDOWN)
        newest = recent[:1]
        if newest and (now - newest.create_date).total_seconds() < cooldown:
            raise SudiOtpRateLimited(_(
                "A code was just sent. Wait a moment before asking for another."
            ))

    @api.model
    def _sudi_issue(self, identifier, purpose, partner=None):
        """Create, send and return an OTP. The plaintext never leaves here."""
        identifier = self.env["stock.picking"]._sudi_normalize_phone(identifier)
        if len(identifier) != 10:
            raise SudiOtpError(_("Enter a valid ten-digit phone number."))
        self._sudi_assert_can_issue(identifier, purpose)

        code = self._sudi_generate_code()
        salt = secrets.token_hex(16)
        otp = self.sudo().create({
            "channel": CHANNEL_WHATSAPP,
            "identifier": identifier,
            "purpose": purpose,
            "salt": salt,
            "code_hash": self._sudi_hash_code(code, salt),
            "expires_at": fields.Datetime.now()
            + timedelta(minutes=self._sudi_param(PARAM_TTL)),
            "partner_id": partner.id if partner else False,
        })
        otp._sudi_deliver(code)
        return otp

    def _sudi_deliver(self, code):
        """Send the code over WhatsApp, recording whether the channel took it."""
        self.ensure_one()
        template = self.env["ir.config_parameter"].sudo().get_param(
            PARAM_BODY_TEMPLATE, "*{{otp}}* is your verification code."
        )
        body = template.replace("{{otp}}", code)
        if self.env["ir.config_parameter"].sudo().get_param(PARAM_LOG_CODES) in (
            "1", "True", "true",
        ):
            # Development only, and off by default.
            _logger.warning("OTP for %s is %s", self.identifier, code)

        sent = self._sudi_send_whatsapp_message(
            recipient_phone=self.identifier,
            body_text=body,
            partner=self.partner_id or None,
        )
        if sent:
            self.sudo().delivery_state = "sent"
            return True
        self.sudo().write({
            "delivery_state": "failed",
            "delivery_error": "The WhatsApp channel did not accept the message.",
        })
        raise SudiOtpUndeliverable(_(
            "We could not send your code right now. Please try again shortly."
        ))

    # ------------------------------------------------------------------
    # verifying
    # ------------------------------------------------------------------
    @api.model
    def _sudi_verify(self, identifier, code, purpose):
        """Consume the newest live code for this number, or raise."""
        identifier = self.env["stock.picking"]._sudi_normalize_phone(identifier)
        otp = self.sudo().search(
            [
                ("identifier", "=", identifier),
                ("purpose", "=", purpose),
                ("consumed_at", "=", False),
                ("locked", "=", False),
            ],
            order="id desc",
            limit=1,
        )
        if not otp:
            raise SudiOtpNotFound(_("No verification code is pending for this number."))
        if otp.expires_at < fields.Datetime.now():
            otp.consumed_at = fields.Datetime.now()
            raise SudiOtpExpired(_("That code has expired. Request a new one."))

        otp.attempts += 1
        candidate = self._sudi_hash_code((code or "").strip(), otp.salt)
        if not hmac.compare_digest(otp.code_hash, candidate):
            if otp.attempts >= self._sudi_param(PARAM_MAX_ATTEMPTS):
                otp.locked = True
                raise SudiOtpRateLimited(_(
                    "Too many incorrect attempts. Request a new code."
                ))
            raise SudiOtpError(_("Incorrect verification code."))

        otp.consumed_at = fields.Datetime.now()
        return otp

    # ------------------------------------------------------------------
    # housekeeping
    # ------------------------------------------------------------------
    @api.autovacuum
    def _sudi_gc_otp(self):
        """Drop spent codes. Nothing here is worth keeping."""
        cutoff = fields.Datetime.now() - timedelta(
            days=self._sudi_param(PARAM_RETENTION_DAYS)
        )
        stale = self.sudo().search([
            "|", ("consumed_at", "<", cutoff), ("expires_at", "<", cutoff)
        ])
        count = len(stale)
        stale.unlink()
        if count:
            _logger.info("Garbage-collected %s expired OTP records", count)
        return count

    @api.constrains("identifier")
    def _check_identifier(self):
        for otp in self:
            if not (otp.identifier or "").strip():
                raise ValidationError(_("An OTP needs a phone number."))

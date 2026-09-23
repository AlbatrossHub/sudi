"""Binaries staged before the intent that references them.

Two phases, because that is what makes a photograph survive a bad connection:
the intent is a few hundred bytes and lands on the worst signal, while the image
retries on its own. It also leaves room for chunked or resumable upload later
without changing any intent route.

The ``sha256`` handed back is the client's proof the bytes arrived intact. It
must keep the local file until the *intent* is acknowledged, not merely until
the upload is: an upload nothing claims is collected after
``sudi_sync.upload_ttl_days``.
"""

import base64
import hashlib
import logging
from uuid import uuid4

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

PARAM_TTL_DAYS = "sudi_sync.upload_ttl_days"
PARAM_MAX_BYTES = "sudi_sync.upload_max_bytes"

DEFAULT_TTL_DAYS = 7
DEFAULT_MAX_BYTES = 8 * 1024 * 1024

ALLOWED_MIMETYPES = ("image/jpeg", "image/png", "image/webp")


class SudiUpload(models.Model):
    _name = "sudi.upload"
    _description = "Sudi Staged Upload"
    _order = "id desc"
    _rec_name = "reference"

    reference = fields.Char(
        required=True,
        index=True,
        readonly=True,
        default=lambda self: uuid4().hex,
        help="What the intent quotes in its upload_ids.",
    )
    user_id = fields.Many2one(
        "res.users", required=True, index=True, ondelete="cascade", readonly=True
    )
    attachment_id = fields.Many2one(
        "ir.attachment", required=True, ondelete="cascade", readonly=True
    )
    sha256 = fields.Char(required=True, index=True, readonly=True)
    file_size = fields.Integer(readonly=True)
    mimetype = fields.Char(readonly=True)
    consumed_model = fields.Char(readonly=True)
    consumed_res_id = fields.Integer(readonly=True)
    consumed_at = fields.Datetime(readonly=True)
    expires_at = fields.Datetime(required=True, index=True, readonly=True)

    _reference_unique = models.Constraint(
        "UNIQUE (reference)", "An upload reference must be unique."
    )

    @api.model
    def _sudi_int_param(self, key, default):
        try:
            value = int(self.env["ir.config_parameter"].sudo().get_param(key, default))
        except (TypeError, ValueError):
            return default
        return value if value > 0 else default

    @api.model
    def _sudi_stage(self, user, datas, filename=None, mimetype=None):
        """Accept one file and return its staged row.

        ``datas`` is base64, as every Odoo binary is. The size limit is checked
        against the decoded bytes, which is what the client actually sent.
        """
        if not datas:
            raise UserError(_("The uploaded file is empty."))
        try:
            raw = base64.b64decode(datas, validate=True)
        except (ValueError, TypeError) as error:
            raise UserError(_("The uploaded file is not valid base64.")) from error
        if not raw:
            raise UserError(_("The uploaded file is empty."))

        max_bytes = self._sudi_int_param(PARAM_MAX_BYTES, DEFAULT_MAX_BYTES)
        if len(raw) > max_bytes:
            raise UserError(_(
                "The uploaded file is %(size)s bytes; the limit is %(limit)s.",
                size=len(raw), limit=max_bytes,
            ))
        if mimetype and mimetype not in ALLOWED_MIMETYPES:
            raise UserError(_("%s is not an accepted image type.", mimetype))

        attachment = self.env["ir.attachment"].sudo().create({
            "name": filename or f"upload_{uuid4().hex[:8]}.jpg",
            "type": "binary",
            "datas": datas,
            "mimetype": mimetype or "image/jpeg",
            # Deliberately unattached: it belongs to no record until an intent
            # claims it, and _cron_prune removes it if none ever does.
            "res_model": self._name,
            "res_id": 0,
        })
        return self.sudo().create({
            "user_id": user.id,
            "attachment_id": attachment.id,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "file_size": len(raw),
            "mimetype": mimetype or "image/jpeg",
            "expires_at": fields.Datetime.add(
                fields.Datetime.now(),
                days=self._sudi_int_param(PARAM_TTL_DAYS, DEFAULT_TTL_DAYS),
            ),
        })

    @api.model
    def _sudi_claim(self, user, references, record):
        """Resolve ``references`` for ``user`` and bind them to ``record``.

        Ownership is checked here, in code: an upload reference is a bearer
        token for a file, and one user must not be able to staple another's
        photograph onto their own delivery.
        """
        references = [reference for reference in (references or []) if reference]
        if not references:
            return self.env["sudi.upload"]
        uploads = self.sudo().search([
            ("reference", "in", references),
            ("user_id", "=", user.id),
            ("consumed_at", "=", False),
        ])
        missing = set(references) - set(uploads.mapped("reference"))
        if missing:
            raise UserError(_(
                "These uploads are unknown, already used, or belong to someone "
                "else: %s", ", ".join(sorted(missing)),
            ))
        expired = uploads.filtered(lambda upload: upload.expires_at < fields.Datetime.now())
        if expired:
            raise UserError(_(
                "These uploads have expired and must be sent again: %s",
                ", ".join(expired.mapped("reference")),
            ))
        # Order the result the way the client listed them, so page 1 of a
        # jangad stays page 1.
        by_reference = {upload.reference: upload for upload in uploads}
        ordered = self.env["sudi.upload"].browse(
            [by_reference[reference].id for reference in references]
        )
        ordered.sudo().write({
            "consumed_model": record._name,
            "consumed_res_id": record.id,
            "consumed_at": fields.Datetime.now(),
        })
        ordered.attachment_id.sudo().write({
            "res_model": record._name,
            "res_id": record.id,
        })
        return ordered

    def _sudi_datas(self):
        """The staged files, in recordset order, as base64."""
        return [upload.attachment_id.datas for upload in self]

    @api.model
    def _cron_prune(self):
        """Collect uploads no intent ever claimed."""
        stale = self.sudo().search([
            ("consumed_at", "=", False),
            ("expires_at", "<", fields.Datetime.now()),
        ])
        if not stale:
            return 0
        count = len(stale)
        attachments = stale.attachment_id
        stale.unlink()
        attachments.sudo().unlink()
        _logger.info("sudi.upload: collected %s unclaimed uploads", count)
        return count

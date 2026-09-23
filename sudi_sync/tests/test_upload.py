import base64
import hashlib
import io

from PIL import Image

from odoo import fields
from odoo.exceptions import UserError

from ..models.sudi_upload import PARAM_MAX_BYTES
from .common import SudiSyncCase


def _png(size=(4, 4)):
    buffer = io.BytesIO()
    Image.new("RGB", size, (255, 255, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


class TestSudiUpload(SudiSyncCase):

    def setUp(self):
        super().setUp()
        self.Uploads = self.env["sudi.upload"]
        self.raw = _png()
        self.datas = base64.b64encode(self.raw)

    _UNSET = object()

    def _sudi_stage(self, user=None, datas=_UNSET, **kwargs):
        # Not `datas or self.datas`: the empty-upload test passes b"" on purpose.
        if datas is self._UNSET:
            datas = self.datas
        return self.Uploads._sudi_stage(user or self.operator, datas, **kwargs)

    def test_staging_returns_the_digest_of_the_bytes_sent(self):
        upload = self._sudi_stage(mimetype="image/png")
        self.assertEqual(upload.sha256, hashlib.sha256(self.raw).hexdigest())
        self.assertEqual(upload.file_size, len(self.raw))
        self.assertTrue(upload.reference)

    def test_an_empty_upload_is_refused(self):
        with self.assertRaises(UserError):
            self._sudi_stage(datas=b"")

    def test_a_non_image_type_is_refused(self):
        with self.assertRaises(UserError):
            self._sudi_stage(mimetype="application/pdf")

    def test_an_oversized_upload_is_refused(self):
        self._set_param(PARAM_MAX_BYTES, "10")
        with self.assertRaises(UserError):
            self._sudi_stage()

    def test_claiming_binds_the_file_to_the_record(self):
        receipt = self._pending_receipt()
        upload = self._sudi_stage()

        claimed = self.Uploads._sudi_claim(self.operator, [upload.reference], receipt)

        self.assertEqual(claimed, upload)
        self.assertEqual(upload.consumed_model, "stock.picking")
        self.assertEqual(upload.consumed_res_id, receipt.id)
        self.assertTrue(upload.consumed_at)
        self.assertEqual(upload.attachment_id.res_model, "stock.picking")
        self.assertEqual(upload.attachment_id.res_id, receipt.id)

    def test_claiming_preserves_the_order_the_client_listed(self):
        # Page one of a jangad has to stay page one.
        receipt = self._pending_receipt()
        first = self._sudi_stage()
        second = self._sudi_stage()
        references = [second.reference, first.reference]

        claimed = self.Uploads._sudi_claim(self.operator, references, receipt)

        self.assertEqual(claimed.ids, [second.id, first.id])

    def test_an_upload_cannot_be_claimed_twice(self):
        receipt = self._pending_receipt()
        upload = self._sudi_stage()
        self.Uploads._sudi_claim(self.operator, [upload.reference], receipt)
        with self.assertRaises(UserError):
            self.Uploads._sudi_claim(self.operator, [upload.reference], receipt)

    def test_one_user_cannot_claim_anothers_upload(self):
        # A reference is a bearer token for a file; ownership is checked in
        # code because no record rule can express it.
        receipt = self._pending_receipt()
        theirs = self._sudi_stage(user=self.operator_2)
        with self.assertRaises(UserError):
            self.Uploads._sudi_claim(self.operator, [theirs.reference], receipt)

    def test_an_unknown_reference_is_refused(self):
        receipt = self._pending_receipt()
        with self.assertRaises(UserError):
            self.Uploads._sudi_claim(self.operator, ["not-a-reference"], receipt)

    def test_an_expired_upload_must_be_sent_again(self):
        receipt = self._pending_receipt()
        upload = self._sudi_stage()
        upload.sudo().write({
            "expires_at": fields.Datetime.subtract(fields.Datetime.now(), days=1)
        })
        with self.assertRaises(UserError):
            self.Uploads._sudi_claim(self.operator, [upload.reference], receipt)

    def test_claiming_nothing_is_not_an_error(self):
        receipt = self._pending_receipt()
        self.assertFalse(self.Uploads._sudi_claim(self.operator, [], receipt))
        self.assertFalse(self.Uploads._sudi_claim(self.operator, None, receipt))

    def test_pruning_collects_unclaimed_expired_uploads(self):
        upload = self._sudi_stage()
        attachment = upload.attachment_id
        upload.sudo().write({
            "expires_at": fields.Datetime.subtract(fields.Datetime.now(), days=1)
        })

        self.assertEqual(self.Uploads._cron_prune(), 1)

        self.assertFalse(upload.exists())
        self.assertFalse(attachment.exists())

    def test_pruning_keeps_a_claimed_upload(self):
        receipt = self._pending_receipt()
        upload = self._sudi_stage()
        self.Uploads._sudi_claim(self.operator, [upload.reference], receipt)
        upload.sudo().write({
            "expires_at": fields.Datetime.subtract(fields.Datetime.now(), days=1)
        })
        self.assertEqual(self.Uploads._cron_prune(), 0)
        self.assertTrue(upload.exists())

    def test_the_staged_bytes_come_back_for_the_intent(self):
        upload = self._sudi_stage()
        self.assertEqual(base64.b64decode(upload._sudi_datas()[0]), self.raw)

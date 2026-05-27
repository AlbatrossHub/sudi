import base64

from odoo import _
from odoo.exceptions import UserError
from odoo.http import Controller, request, route


ALLOWED_IMAGE_MIMETYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
}


class SudiDiamondJangadController(Controller):
    @route(
        "/diamond/jangad/upload",
        type="http",
        auth="public",
        website=True,
        sitemap=False,
        methods=["GET"],
    )
    def jangad_upload_form(self, **kwargs):
        return request.render("diamond.jangad_upload_form", {
            "csrf_token": request.csrf_token(),
            "error": kwargs.get("error"),
            "phone": kwargs.get("phone", ""),
        })

    @route(
        "/diamond/jangad/upload",
        type="http",
        auth="public",
        website=True,
        sitemap=False,
        methods=["POST"],
    )
    def jangad_upload_submit(self, **post):
        phone = (post.get("phone") or "").strip()
        upload = post.get("jangad_image")
        error = self._validate_upload(phone, upload)
        if error:
            return self.jangad_upload_form(error=error, phone=phone)

        image_data = upload.read()
        if not image_data:
            return self.jangad_upload_form(error=_("Please upload a non-empty Jangad image."), phone=phone)

        try:
            receipt = request.env["stock.picking"].sudo().sudi_create_public_jangad_receipt(
                phone,
                base64.b64encode(image_data),
            )
        except UserError as error:
            return self.jangad_upload_form(error=error.args[0], phone=phone)

        return request.render("diamond.jangad_upload_success", {
            "receipt": receipt,
            "partner_found": bool(receipt.partner_id),
        })

    def _validate_upload(self, phone, upload):
        if not phone:
            return _("Please enter your phone number.")
        if not upload or not getattr(upload, "filename", ""):
            return _("Please upload a Jangad image.")
        if upload.mimetype not in ALLOWED_IMAGE_MIMETYPES:
            return _("Please upload a PNG, JPEG, or WebP image.")
        return False

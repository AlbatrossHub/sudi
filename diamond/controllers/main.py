import base64
import json
from urllib.parse import quote

from odoo import _
from odoo.exceptions import UserError
from odoo.http import Controller, request, route
from odoo.tools.image import image_process
from odoo.tools.misc import file_open


ALLOWED_IMAGE_MIMETYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
}


class SudiDiamondJangadController(Controller):
    def _get_jangad_upload_url(self):
        return request.httprequest.url_root.rstrip("/") + request.env["ir.http"]._url_for("/diamond/jangad/upload")

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
        "/diamond/jangad/manifest.webmanifest",
        type="http",
        auth="public",
        website=True,
        sitemap=False,
        methods=["GET"],
        readonly=True,
    )
    def jangad_webmanifest(self):
        manifest = {
            "name": _("Jangad Upload"),
            "short_name": _("Jangad"),
            "description": _("Upload Jangad images for diamond job-work receipts."),
            "scope": request.env["ir.http"]._url_for("/diamond/jangad/"),
            "start_url": request.env["ir.http"]._url_for("/diamond/jangad/upload"),
            "display": "standalone",
            "background_color": "#ffffff",
            "theme_color": "#875A7B",
            "prefer_related_applications": False,
            "icons": [
                {
                    "src": "/diamond/jangad/icon/192.png",
                    "sizes": "192x192",
                    "type": "image/png",
                    "purpose": "any maskable",
                },
                {
                    "src": "/diamond/jangad/icon/512.png",
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "any maskable",
                },
            ],
        }
        return request.make_response(
            json.dumps(manifest),
            [("Content-Type", "application/manifest+json")],
        )

    @route(
        "/diamond/jangad/icon/<int:size>.png",
        type="http",
        auth="public",
        website=True,
        sitemap=False,
        methods=["GET"],
        readonly=True,
    )
    def jangad_icon(self, size=192):
        size = 512 if size >= 512 else 192
        with file_open("diamond/static/description/icon.svg", "rb") as fp:
            image = image_process(fp.read(), size=(size, size), expand=True)
        return request.make_response(
            image,
            [
                ("Content-Type", "image/png"),
                ("Cache-Control", "public, max-age=604800"),
            ],
        )

    @route(
        "/diamond/jangad/service-worker.js",
        type="http",
        auth="public",
        website=True,
        sitemap=False,
        methods=["GET"],
        readonly=True,
    )
    def jangad_service_worker(self):
        with file_open("diamond/static/src/js/jangad_service_worker.js", "r") as fp:
            body = fp.read()
        return request.make_response(
            body,
            [
                ("Content-Type", "text/javascript"),
                ("Service-Worker-Allowed", request.env["ir.http"]._url_for("/diamond/jangad/")),
            ],
        )

    @route(
        "/diamond/jangad/offline",
        type="http",
        auth="public",
        website=True,
        sitemap=False,
        methods=["GET"],
        readonly=True,
    )
    def jangad_offline(self):
        return request.render("diamond.jangad_upload_offline")

    @route(
        "/diamond/jangad/upload/qr",
        type="http",
        auth="public",
        website=True,
        sitemap=False,
        methods=["GET"],
        readonly=True,
    )
    def jangad_upload_qr(self):
        upload_url = self._get_jangad_upload_url()
        qr_src = "/report/barcode/?barcode_type=QR&value=%s&width=320&height=320" % quote(upload_url, safe="")
        return request.render("diamond.jangad_upload_qr", {
            "upload_url": upload_url,
            "qr_src": qr_src,
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
            receipt = self._create_receipt_from_upload(phone, image_data)
        except UserError as error:
            return self.jangad_upload_form(error=error.args[0], phone=phone)

        return request.render("diamond.jangad_upload_success", {
            "receipt": receipt,
            "partner_found": bool(receipt.partner_id),
        })

    @route(
        "/diamond/jangad/upload/json",
        type="http",
        auth="public",
        website=True,
        sitemap=False,
        methods=["POST"],
    )
    def jangad_upload_json(self, **post):
        phone = (post.get("phone") or "").strip()
        upload = post.get("jangad_image")
        error = self._validate_upload(phone, upload)
        if error:
            return request.make_json_response({"success": False, "error": error}, status=400)

        image_data = upload.read()
        if not image_data:
            return request.make_json_response({
                "success": False,
                "error": _("Please upload a non-empty Jangad image."),
            }, status=400)

        try:
            receipt = self._create_receipt_from_upload(phone, image_data)
        except UserError as error:
            return request.make_json_response({"success": False, "error": error.args[0]}, status=400)

        return request.make_json_response({
            "success": True,
            "receipt_name": receipt.name,
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

    def _create_receipt_from_upload(self, phone, image_data):
        return request.env["stock.picking"].sudo().sudi_create_public_jangad_receipt(
            phone,
            base64.b64encode(image_data),
        )

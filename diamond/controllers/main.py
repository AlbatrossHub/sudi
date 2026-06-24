import base64
import json
from urllib.parse import quote

from odoo import _
from odoo.exceptions import UserError
from odoo.http import Controller, request, route
from odoo.tools.misc import file_open


ALLOWED_IMAGE_MIMETYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
}

JANGAD_PWA_ICON_VERSION = "2"
JANGAD_BASE = "/jangad"
JANGAD_LEGACY_BASE = "/diamond/jangad"


class SudiDiamondJangadController(Controller):
    def _get_jangad_upload_url(self):
        return request.httprequest.url_root.rstrip("/") + request.env["ir.http"]._url_for(JANGAD_BASE)

    def _get_stock_picking_sudo(self):
        return request.env["stock.picking"].sudo()

    def _get_portal_partner(self):
        user = request.env.user
        if user._is_public() or not user.share:
            return request.env["res.partner"]
        return user.partner_id.sudo()

    def _get_portal_phone(self, partner):
        commercial_partner = partner.commercial_partner_id
        phone = partner.mobile or partner.phone or commercial_partner.mobile or commercial_partner.phone or ""
        return self._get_stock_picking_sudo()._sudi_normalize_phone(phone)

    def _get_address_suggestions(self, phone=None, partner=None):
        StockPicking = self._get_stock_picking_sudo()
        phone_digits = StockPicking._sudi_normalize_phone(phone)
        if phone and len(phone_digits) != 10:
            return []
        return StockPicking.sudi_get_public_pickup_address_suggestions(phone=phone, partner=partner)

    def _get_pickup_address_values(self, post):
        pickup_address_mode = (post.get("pickup_address_mode") or "").strip()
        pickup_address_id = (post.get("pickup_address_id") or "").strip()
        manual_pickup_address = (post.get("manual_pickup_address") or "").strip()
        if pickup_address_mode == "manual":
            pickup_address_id = ""
        else:
            manual_pickup_address = ""
        return {
            "pickup_address_id": pickup_address_id,
            "pickup_address_mode": "manual" if manual_pickup_address else "existing",
            "manual_pickup_address": manual_pickup_address,
        }

    @route(
        [JANGAD_BASE, f"{JANGAD_LEGACY_BASE}/upload"],
        type="http",
        auth="public",
        website=True,
        sitemap=False,
        methods=["GET"],
    )
    def jangad_upload_form(self, **kwargs):
        phone = kwargs.get("phone")
        portal_partner = self._get_portal_partner()
        if phone is None and portal_partner:
            phone = self._get_portal_phone(portal_partner)
        phone = phone or ""

        address_suggestions = kwargs.get("address_suggestions")
        if address_suggestions is None:
            address_suggestions = self._get_address_suggestions(phone=phone, partner=portal_partner)

        pickup_address_id = kwargs.get("pickup_address_id") or ""
        manual_pickup_address = kwargs.get("manual_pickup_address") or ""
        if not pickup_address_id and address_suggestions and not manual_pickup_address:
            pickup_address_id = str(address_suggestions[0]["id"])

        return request.render("diamond.jangad_upload_form", {
            "csrf_token": request.csrf_token(),
            "error": kwargs.get("error"),
            "phone": phone,
            "pickup_address_id": pickup_address_id,
            "pickup_address_mode": "manual" if manual_pickup_address else "existing",
            "manual_pickup_address": manual_pickup_address,
            "address_suggestions": address_suggestions,
            "address_suggestions_json": json.dumps(address_suggestions),
        })

    @route(
        [f"{JANGAD_BASE}/address_suggestions", f"{JANGAD_LEGACY_BASE}/address_suggestions"],
        type="http",
        auth="public",
        website=True,
        sitemap=False,
        methods=["GET"],
        readonly=True,
    )
    def jangad_address_suggestions(self, **kwargs):
        phone = (kwargs.get("phone") or "").strip()
        StockPicking = self._get_stock_picking_sudo()
        phone_digits = StockPicking._sudi_normalize_phone(phone)
        if len(phone_digits) != 10:
            return request.make_json_response({
                "success": True,
                "phone": phone_digits,
                "addresses": [],
            })
        return request.make_json_response({
            "success": True,
            "phone": phone_digits,
            "addresses": self._get_address_suggestions(phone=phone_digits),
        })

    def _jangad_pwa_icon_src(self, size):
        return f"{JANGAD_BASE}/icon/{size}.png?v={JANGAD_PWA_ICON_VERSION}"

    @route(
        [f"{JANGAD_BASE}/manifest.webmanifest", f"{JANGAD_LEGACY_BASE}/manifest.webmanifest"],
        type="http",
        auth="public",
        website=True,
        sitemap=False,
        methods=["GET"],
        readonly=True,
    )
    def jangad_webmanifest(self):
        manifest = {
            "id": request.env["ir.http"]._url_for(f"{JANGAD_BASE}/"),
            "name": "SDPPL",
            "short_name": "SDPPL",
            "description": "Upload Jangad images for diamond job-work receipts.",
            "scope": request.env["ir.http"]._url_for(f"{JANGAD_BASE}/"),
            "start_url": request.env["ir.http"]._url_for(JANGAD_BASE),
            "display": "standalone",
            "background_color": "#ffffff",
            "theme_color": "#875A7B",
            "prefer_related_applications": False,
            "icons": [
                {
                    "src": self._jangad_pwa_icon_src(192),
                    "sizes": "192x192",
                    "type": "image/png",
                    "purpose": "any",
                },
                {
                    "src": self._jangad_pwa_icon_src(512),
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "any",
                },
            ],
        }
        return request.make_json_response(
            manifest,
            {
                "Content-Type": "application/manifest+json",
                "Cache-Control": "no-store",
            },
        )

    @route(
        [f"{JANGAD_BASE}/icon/<int:size>.png", f"{JANGAD_LEGACY_BASE}/icon/<int:size>.png"],
        type="http",
        auth="public",
        website=True,
        sitemap=False,
        methods=["GET"],
        readonly=True,
    )
    def jangad_icon(self, size=192):
        size = 512 if size >= 512 else 192
        with file_open(f"diamond/static/src/img/jangad_pwa_icon_{size}.png", "rb") as fp:
            image = fp.read()
        return request.make_response(
            image,
            [
                ("Content-Type", "image/png"),
                ("Cache-Control", "no-cache, must-revalidate"),
                ("Content-Security-Policy", "default-src 'self'"),
            ],
        )

    @route(
        [f"{JANGAD_BASE}/service-worker.js", f"{JANGAD_LEGACY_BASE}/service-worker.js"],
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
                ("Cache-Control", "no-cache"),
                ("Service-Worker-Allowed", request.env["ir.http"]._url_for(f"{JANGAD_BASE}/")),
            ],
        )

    @route(
        [f"{JANGAD_BASE}/offline", f"{JANGAD_LEGACY_BASE}/offline"],
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
        [f"{JANGAD_BASE}/qr", f"{JANGAD_LEGACY_BASE}/upload/qr"],
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
        [JANGAD_BASE, f"{JANGAD_LEGACY_BASE}/upload"],
        type="http",
        auth="public",
        website=True,
        sitemap=False,
        methods=["POST"],
    )
    def jangad_upload_submit(self, **post):
        phone = (post.get("phone") or "").strip()
        upload = post.get("jangad_image")
        pickup_values = self._get_pickup_address_values(post)
        error = self._validate_upload(phone, upload, **pickup_values)
        if error:
            return self.jangad_upload_form(error=error, phone=phone, **pickup_values)

        image_data = upload.read()
        if not image_data:
            return self.jangad_upload_form(
                error=_("Please upload a non-empty Jangad image."),
                phone=phone,
                **pickup_values,
            )

        try:
            receipt = self._create_receipt_from_upload(phone, image_data, **pickup_values)
        except UserError as error:
            return self.jangad_upload_form(error=error.args[0], phone=phone, **pickup_values)

        return request.render("diamond.jangad_upload_success", {
            "receipt": receipt,
            "partner_found": bool(receipt.partner_id),
        })

    @route(
        [f"{JANGAD_BASE}/json", f"{JANGAD_LEGACY_BASE}/upload/json"],
        type="http",
        auth="public",
        website=True,
        sitemap=False,
        methods=["POST"],
    )
    def jangad_upload_json(self, **post):
        phone = (post.get("phone") or "").strip()
        upload = post.get("jangad_image")
        pickup_values = self._get_pickup_address_values(post)
        error = self._validate_upload(phone, upload, **pickup_values)
        if error:
            return request.make_json_response({"success": False, "error": error}, status=400)

        image_data = upload.read()
        if not image_data:
            return request.make_json_response({
                "success": False,
                "error": _("Please upload a non-empty Jangad image."),
            }, status=400)

        try:
            receipt = self._create_receipt_from_upload(phone, image_data, **pickup_values)
        except UserError as error:
            return request.make_json_response({"success": False, "error": error.args[0]}, status=400)

        return request.make_json_response({
            "success": True,
            "receipt_name": receipt.name,
            "partner_found": bool(receipt.partner_id),
            "pickup_address": receipt.sudi_pickup_address,
        })

    def _validate_upload(
        self,
        phone,
        upload,
        pickup_address_id=False,
        pickup_address_mode=False,
        manual_pickup_address=False,
    ):
        if not phone:
            return _("Please enter your phone number.")
        if len(self._get_stock_picking_sudo()._sudi_normalize_phone(phone)) != 10:
            return _("Please enter a valid 10-digit phone number.")
        if not pickup_address_id and not manual_pickup_address:
            return _("Please select or enter a pickup address.")
        if not upload or not getattr(upload, "filename", ""):
            return _("Please upload a Jangad image.")
        if upload.mimetype not in ALLOWED_IMAGE_MIMETYPES:
            return _("Please upload a PNG, JPEG, or WebP image.")
        return False

    def _create_receipt_from_upload(
        self,
        phone,
        image_data,
        pickup_address_id=False,
        pickup_address_mode=False,
        manual_pickup_address=False,
    ):
        return request.env["stock.picking"].sudo().sudi_create_public_jangad_receipt(
            phone,
            base64.b64encode(image_data),
            pickup_address_id=pickup_address_id,
            manual_pickup_address=manual_pickup_address,
        )

from odoo import Command
from odoo.addons.stock.tests.common import TestStockCommon


class TestSudiDiamondJobWork(TestStockCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({
            "name": "Diamond Customer",
            "country_id": cls.env.ref("base.in").id,
            "state_id": cls.env.ref("base.state_in_gj").id,
            "l10n_in_gst_treatment": "unregistered",
        })
        cls.product = cls.env.ref("sudi.product_customer_diamond_parcel")
        cls.job_type = cls.env.ref("sudi.job_type_laser_inscription")
        cls.job_type.base_price = 100.0
        cls.picking_type_out.create_backorder = "always"

    def _create_receipt(self, qty=100.0, pcs=100.0, carats=25.0):
        receipt = self.env["stock.picking"].create({
            "partner_id": self.partner.id,
            "picking_type_id": self.picking_type_in.id,
            "location_id": self.supplier_location.id,
            "location_dest_id": self.stock_location.id,
            "sudi_is_diamond_job_work": True,
            "move_ids": [Command.create({
                "name": self.product.display_name,
                "product_id": self.product.id,
                "product_uom_qty": qty,
                "product_uom": self.product.uom_id.id,
                "location_id": self.supplier_location.id,
                "location_dest_id": self.stock_location.id,
                "sudi_sr": 1,
                "sudi_size": "1.00 MM",
                "sudi_pcs_qty": pcs,
                "sudi_carats": carats,
                "sudi_job_type_id": self.job_type.id,
                "sudi_remarks": "Test parcel",
            })],
        })
        receipt.action_confirm()
        receipt.move_ids.quantity = qty
        receipt.move_ids.picked = True
        receipt.button_validate()
        return receipt

    def test_receipt_validation_creates_delivery(self):
        receipt = self._create_receipt()

        self.assertEqual(receipt.state, "done")
        self.assertEqual(len(receipt.sudi_delivery_ids), 1)
        delivery = receipt.sudi_delivery_ids
        self.assertEqual(delivery.picking_type_id, self.picking_type_out)
        self.assertEqual(delivery.partner_id, self.partner)
        self.assertEqual(delivery.move_ids.sudi_origin_receipt_move_id, receipt.move_ids)
        self.assertEqual(delivery.move_ids.sudi_job_type_id, self.job_type)
        self.assertEqual(delivery.move_ids.sudi_pcs_qty, 100.0)
        self.assertEqual(delivery.move_ids.sudi_carats, 25.0)

    def test_partial_delivery_preserves_diamond_fields_on_backorder(self):
        receipt = self._create_receipt()
        delivery = receipt.sudi_delivery_ids

        delivery.move_ids.quantity = 50.0
        delivery.move_ids.picked = True
        delivery.button_validate()

        self.assertEqual(delivery.state, "done")
        self.assertEqual(delivery.move_ids.sudi_pcs_qty, 50.0)
        self.assertEqual(delivery.move_ids.sudi_carats, 12.5)
        self.assertEqual(len(delivery.backorder_ids), 1)
        backorder_move = delivery.backorder_ids.move_ids
        self.assertEqual(backorder_move.sudi_pcs_qty, 50.0)
        self.assertEqual(backorder_move.sudi_carats, 12.5)
        self.assertEqual(backorder_move.sudi_job_type_id, self.job_type)

    def test_partner_special_price_overrides_base_price_on_invoice(self):
        self.env["sudi.diamond.partner.service.price"].create({
            "partner_id": self.partner.commercial_partner_id.id,
            "job_type_id": self.job_type.id,
            "price": 80.0,
        })
        receipt = self._create_receipt()
        delivery = receipt.sudi_delivery_ids
        delivery.move_ids.quantity = 100.0
        delivery.move_ids.picked = True
        delivery.button_validate()

        action = receipt.action_sudi_create_invoice()
        invoice = self.env["account.move"].browse(action["res_id"])

        self.assertEqual(invoice.move_type, "out_invoice")
        self.assertEqual(invoice.sudi_receipt_id, receipt)
        self.assertEqual(invoice.sudi_delivery_ids, delivery)
        self.assertEqual(invoice.invoice_line_ids.price_unit, 80.0)
        self.assertEqual(invoice.invoice_line_ids.quantity, 100.0)
        self.assertEqual(delivery.move_ids.sudi_invoice_line_id, invoice.invoice_line_ids)

    def test_job_type_base_price_used_without_partner_special_price(self):
        receipt = self._create_receipt(qty=10.0, pcs=10.0, carats=2.5)
        delivery = receipt.sudi_delivery_ids
        delivery.move_ids.quantity = 10.0
        delivery.move_ids.picked = True
        delivery.button_validate()

        action = receipt.action_sudi_create_invoice()
        invoice = self.env["account.move"].browse(action["res_id"])

        self.assertEqual(invoice.invoice_line_ids.price_unit, 100.0)
        self.assertEqual(invoice.invoice_line_ids.quantity, 10.0)

    def test_inactive_job_type_can_be_configured(self):
        inactive = self.job_type.copy({"name": "Inactive Laser Inscription", "active": False})
        self.assertFalse(inactive.active)

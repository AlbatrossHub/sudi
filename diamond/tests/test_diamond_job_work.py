from odoo import Command
from odoo.exceptions import UserError
from odoo.addons.stock.tests.common import TestStockCommon


class TestSudiDiamondJobWork(TestStockCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product = cls.env.ref("diamond.product_customer_diamond_parcel")
        cls.job_type = cls.env.ref("diamond.job_type_laser_inscription")
        cls.job_type.base_price = 100.0
        cls.partner = cls.env["res.partner"].create({
            "name": "Diamond Customer",
            "country_id": cls.env.ref("base.in").id,
            "state_id": cls.env.ref("base.state_in_gj").id,
            "l10n_in_gst_treatment": "unregistered",
        })
        cls.picking_type_out.create_backorder = "always"

    def _get_partner_price_line(self, partner=None, job_type=None):
        partner = (partner or self.partner).commercial_partner_id
        job_type = job_type or self.job_type
        return partner.sudi_diamond_service_price_ids.filtered(
            lambda line: line.job_type_id == job_type and line.company_id == (job_type.company_id or self.env.company)
        )[:1]

    def _prepare_receipt_move_command(self, qty=100.0, pcs=100.0, carats=25.0, sr=1, job_type=None):
        return Command.create({
            "name": self.product.display_name,
            "product_id": self.product.id,
            "product_uom_qty": qty,
            "product_uom": self.product.uom_id.id,
            "location_id": self.supplier_location.id,
            "location_dest_id": self.stock_location.id,
            "sudi_sr": sr,
            "sudi_size": "1.00 MM",
            "sudi_pcs_qty": pcs,
            "sudi_carats": carats,
            "sudi_job_type_id": (job_type or self.job_type).id,
            "sudi_remarks": "Test parcel",
        })

    def _create_receipt(self, qty=100.0, pcs=100.0, carats=25.0, partner=None, move_commands=None):
        partner = partner or self.partner
        receipt = self.env["stock.picking"].create({
            "partner_id": partner.id,
            "picking_type_id": self.picking_type_in.id,
            "location_id": self.supplier_location.id,
            "location_dest_id": self.stock_location.id,
            "sudi_is_diamond_job_work": True,
            "move_ids": move_commands or [self._prepare_receipt_move_command(qty, pcs, carats)],
        })
        receipt.action_confirm()
        for move in receipt.move_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
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

    def test_billing_details_one_line_per_receipt_move(self):
        receipt = self._create_receipt(
            move_commands=[
                self._prepare_receipt_move_command(qty=5.0, pcs=5.0, carats=1.0, sr=1),
                self._prepare_receipt_move_command(qty=7.0, pcs=7.0, carats=2.0, sr=2),
            ],
        )

        billing_lines = receipt.sudi_billing_line_ids.filtered(lambda line: line.job_type_id == self.job_type)

        self.assertEqual(len(billing_lines), 2)
        self.assertEqual(
            billing_lines.sorted(key=lambda line: line.sudi_sr).mapped("quantity"),
            [5.0, 7.0],
        )
        self.assertEqual(billing_lines.receipt_move_id, receipt.move_ids.sorted(key=lambda move: move.sudi_sr))
        self.assertEqual(billing_lines.mapped("price_unit"), [100.0, 100.0])
        self.assertEqual(billing_lines.mapped("price_source"), ["partner", "partner"])

    def test_manual_billing_price_is_preserved_after_recompute(self):
        receipt = self._create_receipt(qty=10.0, pcs=10.0, carats=2.5)
        billing_line = receipt.sudi_billing_line_ids.filtered(lambda line: line.job_type_id == self.job_type)
        billing_line.write({
            "price_unit": 77.0,
            "manual_price": True,
            "price_source": "manual",
        })

        receipt.action_sudi_recompute_billing_details()

        self.assertEqual(billing_line.price_unit, 77.0)
        self.assertEqual(billing_line.price_source, "manual")

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

    def test_partial_delivery_invoices_delivered_quantity_only(self):
        receipt = self._create_receipt()
        delivery = receipt.sudi_delivery_ids

        delivery.move_ids.quantity = 50.0
        delivery.move_ids.picked = True
        delivery.button_validate()

        action = receipt.action_sudi_create_invoice()
        invoice = self.env["account.move"].browse(action["res_id"])
        billing_line = receipt.sudi_billing_line_ids.filtered(lambda line: line.job_type_id == self.job_type)

        self.assertEqual(invoice.invoice_line_ids.quantity, 50.0)
        self.assertEqual(invoice.invoice_line_ids.sudi_billing_line_id, billing_line)
        self.assertEqual(billing_line.invoice_line_id, invoice.invoice_line_ids)
        self.assertEqual(invoice.invoice_line_ids.sudi_stock_move_id, delivery.move_ids)
        with self.assertRaises(UserError):
            receipt.action_sudi_create_invoice()

    def test_partner_special_price_overrides_base_price_on_invoice(self):
        self._get_partner_price_line().price = 80.0
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
        self.assertEqual(invoice.invoice_line_ids.sudi_stock_move_id, delivery.move_ids)

    def test_same_job_type_receipt_lines_create_separate_invoice_lines(self):
        receipt = self._create_receipt(
            move_commands=[
                self._prepare_receipt_move_command(qty=5.0, pcs=5.0, carats=1.0, sr=1),
                self._prepare_receipt_move_command(qty=7.0, pcs=7.0, carats=2.0, sr=2),
            ],
        )
        delivery = receipt.sudi_delivery_ids
        for move in delivery.move_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
        delivery.button_validate()

        action = receipt.action_sudi_create_invoice()
        invoice = self.env["account.move"].browse(action["res_id"])

        self.assertEqual(len(invoice.invoice_line_ids), 2)
        self.assertEqual(
            invoice.invoice_line_ids.sorted(key=lambda line: line.quantity).mapped("quantity"),
            [5.0, 7.0],
        )
        self.assertEqual(len(receipt.sudi_billing_line_ids.filtered("invoice_line_id")), 2)
        self.assertEqual(
            delivery.move_ids.sorted(key=lambda move: move.sudi_sr).sudi_invoice_line_id,
            invoice.invoice_line_ids.sorted(key=lambda line: line.quantity),
        )

    def test_job_type_base_price_used_without_partner_special_price(self):
        self._get_partner_price_line().unlink()
        receipt = self._create_receipt(qty=10.0, pcs=10.0, carats=2.5)
        delivery = receipt.sudi_delivery_ids
        delivery.move_ids.quantity = 10.0
        delivery.move_ids.picked = True
        delivery.button_validate()

        action = receipt.action_sudi_create_invoice()
        invoice = self.env["account.move"].browse(action["res_id"])

        self.assertEqual(invoice.invoice_line_ids.price_unit, 100.0)
        self.assertEqual(invoice.invoice_line_ids.quantity, 10.0)

    def test_company_partner_gets_default_service_prices(self):
        partner = self.env["res.partner"].create({
            "name": "Default Price Customer",
            "is_company": True,
            "country_id": self.env.ref("base.in").id,
            "state_id": self.env.ref("base.state_in_gj").id,
        })
        active_job_types = self.env["sudi.diamond.job.type"].search([("active", "=", True)])

        self.assertEqual(
            set(partner.sudi_diamond_service_price_ids.mapped("job_type_id").ids),
            set(active_job_types.ids),
        )
        self.assertEqual(
            partner.sudi_diamond_service_price_ids.filtered(lambda line: line.job_type_id == self.job_type).price,
            self.job_type.base_price,
        )

    def test_child_contact_uses_company_price_rows(self):
        company_partner = self.env["res.partner"].create({
            "name": "Parent Diamond Company",
            "is_company": True,
            "country_id": self.env.ref("base.in").id,
            "state_id": self.env.ref("base.state_in_gj").id,
            "l10n_in_gst_treatment": "unregistered",
        })
        self._get_partner_price_line(company_partner).price = 66.0
        child_partner = self.env["res.partner"].create({
            "name": "Buyer Contact",
            "parent_id": company_partner.id,
            "country_id": self.env.ref("base.in").id,
            "state_id": self.env.ref("base.state_in_gj").id,
        })

        self.assertFalse(child_partner.sudi_diamond_service_price_ids)
        self.assertTrue(company_partner.sudi_diamond_service_price_ids)

        receipt = self._create_receipt(qty=10.0, pcs=10.0, carats=2.5, partner=child_partner)
        delivery = receipt.sudi_delivery_ids
        delivery.move_ids.quantity = 10.0
        delivery.move_ids.picked = True
        delivery.button_validate()

        action = receipt.action_sudi_create_invoice()
        invoice = self.env["account.move"].browse(action["res_id"])

        self.assertEqual(invoice.partner_id, company_partner)
        self.assertEqual(invoice.partner_shipping_id, child_partner)
        self.assertEqual(invoice.invoice_line_ids.price_unit, 66.0)

    def test_job_type_invoice_description_used_exactly(self):
        description = "Diamond Job Work Charges"
        self.job_type.invoice_description = description
        receipt = self._create_receipt(qty=10.0, pcs=10.0, carats=2.5)
        delivery = receipt.sudi_delivery_ids
        delivery.move_ids.quantity = 10.0
        delivery.move_ids.picked = True
        delivery.button_validate()

        action = receipt.action_sudi_create_invoice()
        invoice = self.env["account.move"].browse(action["res_id"])

        self.assertEqual(invoice.invoice_line_ids.name, description)

    def test_inactive_job_type_can_be_configured(self):
        inactive = self.job_type.copy({"name": "Inactive Laser Inscription", "active": False})
        self.assertFalse(inactive.active)

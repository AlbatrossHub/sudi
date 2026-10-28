from odoo import Command, fields
from odoo.exceptions import AccessError, UserError

from .test_diamond_job_work import SudiJobWorkCase
from .test_field_event_capture import PIXEL


class SudiMobileV2Case(SudiJobWorkCase):
    """Receipts in the states the v2 office flow works on."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.carat_type = cls.env["sudi.diamond.job.type"].sudo().create({
            "name": "V2 Carat Grading",
            "service_product_id": cls.job_type.service_product_id.id,
            "invoice_basis": "carats",
        })
        cls.partner.sudo().write({"phone": "9876500001"})

    def setUp(self):
        super().setUp()
        # The jangad upload notifies over WhatsApp, which is not under test.
        Picking = type(self.env["stock.picking"])
        for name in ("_sudi_notify_pickup_scheduled", "_sudi_notify_pickup_confirmed"):
            original = getattr(Picking, name)
            setattr(Picking, name, lambda records: None)
            self.addCleanup(setattr, Picking, name, original)

    def _jangad(self, services=None, **kwargs):
        return self.env["stock.picking"].sudi_create_public_jangad_receipt(
            "9876500001", PIXEL,
            manual_pickup_address="12, Mahidharpura, Surat",
            services=services, **kwargs,
        )

    def _collected(self, services=None):
        """A jangad receipt the operator has collected: state draft."""
        receipt = self._jangad(services)
        receipt.sudo().write({"sudi_pickup_datetime": fields.Datetime.now()})
        self.assertEqual(receipt.state, "draft")
        return receipt


class TestSudiServiceRequests(SudiMobileV2Case):

    def test_a_jangad_carries_the_requested_services_and_where_it_came_from(self):
        poster = self.env.user
        receipt = self._jangad(
            services=[
                {"job_type_id": self.job_type.id, "quantity": 120, "remarks": "big first"},
                {"job_type_id": self.carat_type.id, "quantity": 4.5},
            ],
            source="on_behalf",
            posted_by=poster,
        )

        requests = receipt.sudi_service_request_ids
        self.assertEqual(requests.job_type_id, self.job_type | self.carat_type)
        self.assertEqual(requests.mapped("quantity"), [120.0, 4.5])
        self.assertEqual(requests.mapped("unit"), ["pcs", "ct"])
        self.assertEqual(requests[0].remarks, "big first")
        self.assertFalse(requests.move_id)
        self.assertEqual(receipt.sudi_jangad_source, "on_behalf")
        self.assertEqual(receipt.sudi_posted_by_id, poster)
        self.assertTrue(receipt.sudi_has_open_service_requests)

    def test_existing_callers_are_unchanged(self):
        receipt = self._jangad()
        self.assertFalse(receipt.sudi_service_request_ids)
        self.assertFalse(receipt.sudi_jangad_source)
        self.assertFalse(receipt.sudi_posted_by_id)

    def test_a_service_no_longer_offered_refuses_the_whole_jangad(self):
        retired = self.env["sudi.diamond.job.type"].sudo().create({
            "name": "V2 Retired Service",
            "service_product_id": self.job_type.service_product_id.id,
            "active": False,
        })
        before = self.env["stock.picking"].search_count([])
        with self.assertRaises(UserError):
            self._jangad(services=[{"job_type_id": retired.id, "quantity": 1}])
        self.assertEqual(self.env["stock.picking"].search_count([]), before)

    def test_a_negative_quantity_is_refused(self):
        with self.assertRaises(UserError):
            self._jangad(services=[{"job_type_id": self.job_type.id, "quantity": -1}])

    def test_a_pinned_customer_wins_over_the_phone_lookup(self):
        # Two customers sharing a number: the phone lookup finds only the
        # first, so a directory pick has to be able to say which one.
        twin = self.env["res.partner"].create({"name": "Twin Gems", "phone": "9876500001"})
        receipt = self._jangad(partner=twin)
        self.assertEqual(receipt.partner_id, twin)
        self.assertEqual(receipt.sudi_pickup_address_id.parent_id, twin)


class TestSudiAddLinesFromRequests(SudiMobileV2Case):

    def test_each_open_request_becomes_one_item_line(self):
        receipt = self._collected([
            {"job_type_id": self.job_type.id, "quantity": 120, "remarks": "big first"},
            {"job_type_id": self.carat_type.id, "quantity": 4.5},
        ])

        moves = receipt._sudi_add_lines_from_requests()

        self.assertEqual(len(moves), 2)
        pieces, carats = moves
        self.assertEqual(pieces.product_id, self.product)
        self.assertEqual(pieces.sudi_job_type_id, self.job_type)
        self.assertEqual(pieces.sudi_pcs_qty, 120.0)
        self.assertEqual(pieces.product_uom_qty, 120.0)
        self.assertEqual(pieces.sudi_remarks, "big first")
        # A carat-billed service puts its quantity in carats, not pieces.
        self.assertEqual(carats.sudi_carats, 4.5)
        self.assertEqual(carats.sudi_pcs_qty, 0.0)
        self.assertEqual(receipt.sudi_service_request_ids.move_id, moves)
        self.assertFalse(receipt.sudi_has_open_service_requests)
        # The billing details follow, as they do when the form saves.
        self.assertIn(pieces, receipt.sudi_billing_line_ids.receipt_move_id)

    def test_a_second_tap_adds_nothing(self):
        receipt = self._collected([{"job_type_id": self.job_type.id, "quantity": 5}])
        receipt._sudi_add_lines_from_requests()

        again = receipt._sudi_add_lines_from_requests()

        self.assertFalse(again)
        self.assertEqual(len(receipt.move_ids), 1)

    def test_removing_the_line_reopens_the_request(self):
        receipt = self._collected([{"job_type_id": self.job_type.id, "quantity": 5}])
        moves = receipt._sudi_add_lines_from_requests()

        moves.sudo().unlink()

        self.assertFalse(receipt.sudi_service_request_ids.move_id)
        self.assertTrue(receipt.sudi_has_open_service_requests)

    def test_not_before_the_parcel_is_collected(self):
        receipt = self._jangad([{"job_type_id": self.job_type.id, "quantity": 5}])
        self.assertEqual(receipt.state, "sudi_pickup_pending")
        with self.assertRaises(UserError):
            receipt._sudi_add_lines_from_requests()

    def test_not_once_job_work_has_started(self):
        receipt = self._collected([{"job_type_id": self.job_type.id, "quantity": 5}])
        receipt._sudi_add_lines_from_requests()
        receipt.action_confirm()
        self.assertEqual(receipt.state, "assigned")
        receipt.sudi_service_request_ids.sudo().move_id = False
        with self.assertRaises(UserError):
            receipt._sudi_add_lines_from_requests()

    def test_the_form_button_does_the_same(self):
        receipt = self._collected([{"job_type_id": self.job_type.id, "quantity": 5}])
        self.assertTrue(receipt.action_sudi_add_lines_from_requests())
        self.assertEqual(len(receipt.move_ids), 1)


class TestSudiPiecesDriveDemand(SudiMobileV2Case):
    """stock.move.write keeps product_uom_qty in step with sudi_pcs_qty."""

    def _line(self, receipt, pcs=10.0):
        return self.env["stock.move"].create({
            "picking_id": receipt.id,
            "product_id": self.product.id,
            "uom_id": self.product.uom_id.id,
            "location_id": receipt.location_id.id,
            "location_dest_id": receipt.location_dest_id.id,
            "sudi_pcs_qty": pcs,
            "sudi_job_type_id": self.job_type.id,
        })

    def test_a_plain_write_of_pieces_moves_the_demand(self):
        receipt = self._collected()
        move = self._line(receipt)

        move.write({"sudi_pcs_qty": 24.0})

        self.assertEqual(move.product_uom_qty, 24.0)

    def test_editing_a_line_does_not_mark_the_receipt_ready(self):
        # Writing the done quantity of a draft line runs its inverse, which
        # reserves the move and confirms the receipt: editing the table then
        # sent it to job work without the office pressing Mark READY.
        receipt = self._collected()
        move = self._line(receipt)

        move.write({"sudi_pcs_qty": 24.0, "sudi_size": "+11"})

        self.assertEqual(move.state, "draft")
        self.assertEqual(receipt.state, "draft")

    def test_marking_ready_confirms_the_corrected_demand(self):
        receipt = self._collected()
        move = self._line(receipt)
        move.write({"sudi_pcs_qty": 24.0})

        receipt.action_confirm()

        self.assertEqual(receipt.state, "assigned")
        self.assertEqual(move.product_uom_qty, 24.0)
        self.assertEqual(move.quantity, 24.0)

    def test_an_explicit_demand_in_the_same_write_wins(self):
        receipt = self._collected()
        move = self._line(receipt)
        move.write({"sudi_pcs_qty": 24.0, "product_uom_qty": 30.0})
        self.assertEqual(move.product_uom_qty, 30.0)

    def test_a_finished_line_is_left_alone(self):
        receipt = self._create_receipt(qty=10.0, pcs=10.0)
        move = receipt.move_ids
        self.assertEqual(move.state, "done")

        move.write({"sudi_pcs_qty": 12.0})

        self.assertEqual(move.product_uom_qty, 10.0)

    def test_a_split_keeps_the_demand_it_was_given(self):
        # _split writes the remaining pieces itself, proportionally; they must
        # not drag the demand super() already reduced. Only visible when the
        # pieces and the demand differ, so make them differ.
        receipt = self._collected()
        move = self._line(receipt, pcs=20.0)
        move.write({"product_uom_qty": 10.0})
        receipt.action_confirm()

        new_vals = move._split(4.0)

        self.assertEqual(move.product_uom_qty, 6.0)
        self.assertEqual(move.sudi_pcs_qty, 12.0)
        self.assertEqual(new_vals[0]["sudi_pcs_qty"], 8.0)


class TestSudiDepartmentsAndTime(SudiMobileV2Case):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cutting = cls.env["sudi.diamond.job.type"].sudo().create({
            "name": "V2 Cutting", "service_product_id": cls.job_type.service_product_id.id,
        })
        cls.polishing = cls.env["sudi.diamond.job.type"].sudo().create({
            "name": "V2 Polishing", "service_product_id": cls.job_type.service_product_id.id,
        })

    def _in_progress(self):
        receipt = self.env["stock.picking"].create({
            "partner_id": self.partner.id,
            "picking_type_id": self.picking_type_in.id,
            "location_id": self.supplier_location.id,
            "location_dest_id": self.stock_location.id,
            "sudi_is_diamond_job_work": True,
            "sudi_pickup_datetime": fields.Datetime.now(),
            "move_ids": [self._prepare_receipt_move_command()],
        })
        receipt.action_confirm()
        return receipt

    def _employee(self, user=None):
        user = user or self.env.user
        return self.env["hr.employee"].sudo().create({
            "name": user.name, "user_id": user.id, "company_id": self.env.company.id,
        })

    def test_every_department_passed_through_is_recorded(self):
        receipt = self._in_progress()
        receipt.sudi_current_department_id = self.cutting

        receipt._sudi_transfer_department(self.polishing)

        self.assertEqual(receipt.sudi_current_department_id, self.polishing)
        self.assertEqual(receipt.sudi_involved_department_ids, self.cutting | self.polishing)

    def test_a_manual_entry_books_the_time_for_the_user(self):
        receipt = self._in_progress()
        employee = self._employee()
        yesterday = fields.Date.subtract(fields.Date.context_today(receipt), days=1)

        line = receipt._sudi_log_time(self.job_type, 1.5, description="Sorting", date=yesterday)

        self.assertEqual(line.sudi_picking_id, receipt)
        self.assertEqual(line.sudi_job_type_id, self.job_type)
        self.assertEqual(line.unit_amount, 1.5)
        self.assertEqual(line.date, yesterday)
        self.assertEqual(line.name, "Sorting")
        self.assertEqual(line.employee_id, employee)
        self.assertEqual(receipt.sudi_total_hours_spent, 1.5)

    def test_time_needs_a_receipt_in_progress(self):
        self._employee()
        receipt = self._collected()
        with self.assertRaises(UserError):
            receipt._sudi_log_time(self.job_type, 1.0)

    def test_time_needs_a_job_type_and_a_sane_amount(self):
        self._employee()
        receipt = self._in_progress()
        with self.assertRaises(UserError):
            receipt._sudi_log_time(self.env["sudi.diamond.job.type"], 1.0)
        with self.assertRaises(UserError):
            receipt._sudi_log_time(self.job_type, 25.0)
        with self.assertRaises(UserError):
            receipt._sudi_log_time(self.job_type, 0.0)

    def test_time_needs_an_employee_behind_the_user(self):
        receipt = self._in_progress()
        with self.assertRaises(UserError):
            receipt._sudi_log_time(self.job_type, 1.0)

    def test_time_needs_the_job_work_role(self):
        receipt = self._in_progress()
        outsider = self.env["res.users"].sudo().create({
            "name": "V2 Outsider", "login": "v2_outsider",
            "group_ids": [Command.set([self.env.ref("base.group_user").id])],
        })
        self._employee(outsider)
        with self.assertRaises(AccessError):
            receipt.with_user(outsider)._sudi_log_time(self.job_type, 1.0)


class TestSudiReadyProblems(SudiMobileV2Case):

    def test_an_empty_receipt_needs_lines(self):
        receipt = self._collected()
        problems = receipt._sudi_job_work_ready_problems()
        self.assertEqual([problem["field"] for problem in problems], ["lines"])

    def test_every_problem_is_listed_at_once(self):
        receipt = self._collected()
        receipt.partner_id = False
        self.env["stock.move"].create([
            {
                "picking_id": receipt.id,
                "product_id": self.product.id,
                "uom_id": self.product.uom_id.id,
                "location_id": receipt.location_id.id,
                "location_dest_id": receipt.location_dest_id.id,
                "sudi_pcs_qty": 0.0,
            },
            {
                "picking_id": receipt.id,
                "product_id": self.product.id,
                "uom_id": self.product.uom_id.id,
                "location_id": receipt.location_id.id,
                "location_dest_id": receipt.location_dest_id.id,
                "sudi_pcs_qty": 3.0,
                "sudi_job_type_id": self.job_type.id,
            },
        ])

        problems = receipt._sudi_job_work_ready_problems()

        self.assertEqual(
            {(problem["sr"], problem["field"]) for problem in problems},
            {(None, "partner_id"), (1, "job_type_id"), (1, "pcs")},
        )
        self.assertTrue(all(problem["message"] for problem in problems))

    def test_a_complete_receipt_has_none(self):
        receipt = self._collected([{"job_type_id": self.job_type.id, "quantity": 5}])
        receipt._sudi_add_lines_from_requests()
        self.assertEqual(receipt._sudi_job_work_ready_problems(), [])


class TestSudiOtpDevelopmentMode(SudiJobWorkCase):
    """``sudi_auth.otp_dev_no_send``: a copied database can sign customers in without
    WhatsApp, and the switch never weakens a database where it is off."""

    def setUp(self):
        super().setUp()
        Otp = type(self.env["sudi.auth.otp"])
        original = Otp._sudi_send_whatsapp_message
        self.sends = []

        def fake_send(records, **kwargs):
            self.sends.append(kwargs)
            return False  # the connector of a copied database: not connected

        Otp._sudi_send_whatsapp_message = fake_send
        self.addCleanup(setattr, Otp, "_sudi_send_whatsapp_message", original)

    def _otp(self):
        return self.env["sudi.auth.otp"].sudo().create({
            "channel": "whatsapp",
            "identifier": "9876500077",
            "purpose": "login",
            "salt": "00",
            "code_hash": "x",
            "expires_at": fields.Datetime.now(),
        })

    def test_dev_mode_logs_instead_of_sending(self):
        self.env["ir.config_parameter"].sudo().set_str("sudi_auth.otp_dev_no_send", "1")
        otp = self._otp()
        with self.assertLogs("odoo.addons.diamond.models.sudi_auth_otp", level="WARNING") as logs:
            self.assertTrue(otp._sudi_deliver("123456"))
        self.assertIn("123456", logs.output[0])
        self.assertEqual(otp.delivery_state, "sent")
        self.assertFalse(self.sends, "development mode must not attempt a WhatsApp send")

    def test_without_dev_mode_a_dead_channel_still_refuses(self):
        otp = self._otp()
        # assertRaises rolls back to its savepoint, so the "failed" stamp written on the way
        # out is not visible here (the API keeps it: OTP refusals do not roll back).
        with self.assertRaises(UserError):
            otp._sudi_deliver("123456")
        self.assertEqual(len(self.sends), 1, "with the switch off the channel is still tried")

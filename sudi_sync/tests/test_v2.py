from datetime import timedelta

from odoo import Command, fields

from .common import SudiSyncCase


class SudiV2Case(SudiSyncCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.validator = cls.env["res.users"].sudo().create({
            "name": "Meera Office",
            "login": "sudi_sync_validator",
            "group_ids": [Command.set([
                cls.env.ref("diamond.group_sudi_office_validator").id,
            ])],
        })
        cls.job_worker = cls.env["res.users"].sudo().create({
            "name": "Jaya Job Work",
            "login": "sudi_sync_job_worker",
            "group_ids": [Command.set([
                cls.env.ref("diamond.group_sudi_job_work_user").id,
            ])],
        })
        for user in (cls.operator, cls.job_worker):
            cls.env["hr.employee"].sudo().create({
                "name": user.name, "user_id": user.id, "company_id": cls.env.company.id,
            })

    def setUp(self):
        super().setUp()
        Picking = type(self.env["stock.picking"])
        original = Picking._sudi_notify_pickup_scheduled
        Picking._sudi_notify_pickup_scheduled = lambda records: None
        self.addCleanup(setattr, Picking, "_sudi_notify_pickup_scheduled", original)

    def _collected_receipt(self, services=None):
        receipt = self._pending_receipt()
        if services:
            receipt.sudo().write({"sudi_service_request_ids": [
                Command.create(service) for service in services
            ]})
        receipt.with_user(self.operator).action_sudi_confirm_pickup()
        self.assertEqual(receipt.state, "draft")
        return receipt

    def _notices(self, kind=None, user=None, picking=None):
        domain = []
        if kind:
            domain.append(("kind", "=", kind))
        if user:
            domain.append(("user_id", "=", user.id))
        if picking:
            domain.append(("picking_id", "=", picking.id))
        return self.env["sudi.app.notice"].sudo().search(domain)


class TestSudiOfficeScope(SudiV2Case):

    def _office_ids(self, user=None):
        result = self._pull(user=user or self.validator, scopes=["office"])
        return [doc["id"] for doc in result["scopes"]["office"]["upserts"]]

    def test_a_collected_receipt_awaits_the_office(self):
        receipt = self._collected_receipt()
        self._sync_flush()
        self.assertIn(receipt.id, self._office_ids())

    def test_a_pending_pickup_is_not_the_offices_yet(self):
        receipt = self._pending_receipt()
        self._sync_flush()
        self.assertNotIn(receipt.id, self._office_ids())

    def test_marking_ready_leaves_the_office_scope(self):
        receipt = self._collected_receipt([{"job_type_id": self.job_type.id, "quantity": 3}])
        receipt._sudi_add_lines_from_requests()
        receipt.action_confirm()
        self._sync_flush()
        self.assertEqual(receipt.state, "assigned")
        self.assertNotIn(receipt.id, self._office_ids())

    def test_the_office_document_carries_the_table_and_the_requests(self):
        receipt = self._collected_receipt([
            {"job_type_id": self.job_type.id, "quantity": 120, "remarks": "big first"},
        ])
        receipt.sudo().write({
            "sudi_jangad_source": "on_behalf", "sudi_posted_by_id": self.operator.id,
        })
        moves = receipt._sudi_add_lines_from_requests()
        self._sync_flush()

        doc = [
            document
            for document in self._pull(user=self.validator, scopes=["office"])["scopes"]["office"]["upserts"]
            if document["id"] == receipt.id
        ][0]

        self.assertEqual(doc["stage"], "collected")
        self.assertEqual(doc["jangad_source"], "on_behalf")
        self.assertEqual(doc["posted_by"]["id"], self.operator.id)
        self.assertEqual(doc["items"][0]["move_id"], moves.id)
        self.assertEqual(doc["items"][0]["pcs"], 120.0)
        self.assertEqual(doc["services"], [{
            "id": receipt.sudi_service_request_ids.id,
            "job_type": {"id": self.job_type.id, "name": self.job_type.display_name},
            "quantity": 120.0,
            "unit": "pcs",
            "remarks": "big first",
            "move_id": moves.id,
        }])

    def test_a_requested_service_changing_resyncs_its_receipt(self):
        receipt = self._collected_receipt([{"job_type_id": self.job_type.id, "quantity": 1}])
        self._sync_flush()
        before = self._changes(receipt.id)

        receipt.sudi_service_request_ids.sudo().write({"quantity": 2})
        self._sync_flush()

        self.assertGreater(len(self._changes(receipt.id)), len(before))


class TestSudiRicherDocuments(SudiV2Case):

    def test_the_pickup_document_lists_the_requested_services(self):
        receipt = self._pending_receipt()
        receipt.sudo().write({"sudi_service_request_ids": [
            Command.create({"job_type_id": self.job_type.id, "quantity": 7}),
        ]})
        self._sync_flush()

        doc = [
            document for document in self._pull(scopes=["pickup"])["scopes"]["pickup"]["upserts"]
            if document["id"] == receipt.id
        ][0]

        self.assertEqual(doc["services"][0]["quantity"], 7.0)
        self.assertIsNone(doc["services"][0]["move_id"])

    def test_my_hours_are_mine_and_total_hours_are_everyones(self):
        receipt = self._assigned_receipt()
        receipt.with_user(self.operator)._sudi_log_time(self.job_type, 2.0)
        receipt.with_user(self.job_worker)._sudi_log_time(self.job_type, 0.5)
        self._sync_flush()

        def doc_for(user):
            return [
                document
                for document in self._pull(user=user, scopes=["jobwork"])["scopes"]["jobwork"]["upserts"]
                if document["id"] == receipt.id
            ][0]

        mine = doc_for(self.operator)
        theirs = doc_for(self.job_worker)
        self.assertEqual(mine["my_hours"], 2.0)
        self.assertEqual(theirs["my_hours"], 0.5)
        self.assertEqual(mine["total_hours"], 2.5)
        self.assertEqual(mine["items"][0]["move_id"], receipt.move_ids.id)


class TestSudiNotices(SudiV2Case):

    def test_a_new_jangad_tells_every_pickup_person_but_its_poster(self):
        receipt = self.env["stock.picking"].with_user(self.operator).sudo().create({
            "partner_id": self.partner.id,
            "picking_type_id": self.picking_type_in.id,
            "location_id": self.supplier_location.id,
            "location_dest_id": self.stock_location.id,
            "sudi_is_diamond_job_work": True,
            "sudi_pickup_address": "12, Mahidharpura, Surat",
        })

        notices = self._notices("pickup_new", picking=receipt)

        self.assertIn(self.operator_2, notices.user_id)
        self.assertNotIn(self.operator, notices.user_id)
        notice = notices.filtered(lambda record: record.user_id == self.operator_2)
        self.assertEqual(notice.scope, "pickup")
        self.assertIn(receipt.name, notice.title)
        self.assertIn("Mahidharpura", notice.body)

    def test_a_collected_pickup_asks_the_office_to_validate(self):
        receipt = self._collected_receipt()
        notices = self._notices("office_validate", picking=receipt)
        self.assertEqual(notices.user_id, self.validator)
        self.assertIn(self.operator.name, notices.body)
        self.assertEqual(notices.scope, "office")

    def test_ready_job_work_tells_the_job_workers(self):
        receipt = self._collected_receipt([{"job_type_id": self.job_type.id, "quantity": 3}])
        receipt._sudi_add_lines_from_requests()

        receipt.with_user(self.validator).action_confirm()

        notices = self._notices("jobwork_ready", picking=receipt)
        self.assertIn(self.job_worker, notices.user_id)
        self.assertNotIn(self.validator, notices.user_id)

    def test_confirming_what_is_already_in_job_work_rings_nobody(self):
        receipt = self._assigned_receipt()
        self._notices("jobwork_ready", picking=receipt).unlink()

        receipt.action_confirm()

        self.assertFalse(self._notices("jobwork_ready", picking=receipt))

    def test_a_department_transfer_tells_the_other_job_workers(self):
        receipt = self._assigned_receipt()
        polishing = self.env["sudi.diamond.job.type"].sudo().create({
            "name": "Sync Polishing",
            "service_product_id": self.job_type.service_product_id.id,
        })

        receipt.with_user(self.operator)._sudi_transfer_department(polishing)

        notices = self._notices("department_transfer", picking=receipt)
        self.assertIn(self.job_worker, notices.user_id)
        self.assertNotIn(self.operator, notices.user_id)
        self.assertIn("Sync Polishing", notices[0].title)
        self.assertIn(self.operator.name, notices[0].body)

    def test_a_new_delivery_order_tells_the_delivery_people(self):
        receipt = self._assigned_receipt()
        for move in receipt.move_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
        receipt.button_validate()
        delivery = receipt.sudi_delivery_ids

        notices = self._notices("delivery_ready", picking=delivery)

        self.assertTrue(delivery)
        self.assertIn(self.operator, notices.user_id)
        self.assertEqual(notices[0].scope, "delivery")

    def test_a_notice_failure_never_breaks_the_action(self):
        receipt = self._pending_receipt()
        Notice = type(self.env["sudi.app.notice"])
        original = Notice._sudi_text

        def broken(records, kind, picking, **extra):
            raise RuntimeError("the bell is broken")

        Notice._sudi_text = broken
        self.addCleanup(setattr, Notice, "_sudi_text", original)

        with self.assertLogs("odoo.addons.sudi_sync.models.sudi_app_notice", level="ERROR"):
            receipt.with_user(self.operator).action_sudi_confirm_pickup()

        self.assertEqual(receipt.state, "draft")
        self.assertEqual(receipt.sudi_pickup_user_id, self.operator)
        self.assertFalse(self._notices("office_validate", picking=receipt))

    def test_old_notices_are_purged(self):
        receipt = self._pending_receipt()
        notices = self._notices("pickup_new", picking=receipt)
        self.assertTrue(notices)
        old = notices[0]
        self.env.cr.execute(
            "UPDATE sudi_app_notice SET create_date = %s WHERE id = %s",
            (fields.Datetime.now() - timedelta(days=31), old.id),
        )
        self.env["sudi.app.notice"].invalidate_model()

        self.env["sudi.app.notice"]._cron_prune()

        self.assertFalse(old.exists())
        self.assertTrue((notices - old).exists())

    def test_a_person_reads_only_their_own_notices(self):
        self._pending_receipt()
        Notice = self.env["sudi.app.notice"].with_user(self.operator_2)

        visible = Notice.search([])

        self.assertTrue(visible)
        self.assertEqual(visible.user_id, self.operator_2)
        self.assertEqual(visible._sudi_mark_read(), len(visible))
        self.assertEqual(Notice._sudi_unread_count(self.operator_2), 0)

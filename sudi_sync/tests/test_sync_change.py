from datetime import timedelta

from odoo import fields

from ..models.sudi_sync_change import PARAM_LAG_SECONDS, PARAM_PRUNED_THROUGH
from .common import SudiSyncCase


class TestSudiSyncChangeLog(SudiSyncCase):
    """What gets logged, and how little of it."""

    def test_a_new_receipt_is_logged_once(self):
        receipt = self._pending_receipt()
        self._sync_flush()
        changes = self._changes(receipt.id)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes.op, "create")

    def test_many_writes_in_one_transaction_are_one_row(self):
        receipt = self._pending_receipt()
        self._sync_flush()
        for index in range(20):
            receipt.sudi_internal_notes = f"note {index}"
        self._sync_flush()
        # One for the create, one for the whole batch of writes.
        self.assertEqual(len(self._changes(receipt.id)), 2)

    def test_a_create_outranks_a_write_in_the_same_transaction(self):
        receipt = self._pending_receipt()
        receipt.sudi_internal_notes = "edited before anyone saw it"
        self._sync_flush()
        changes = self._changes(receipt.id)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes.op, "create")

    def test_an_item_line_change_invalidates_its_picking(self):
        receipt = self._assigned_receipt()
        self._sync_flush()
        before = len(self._changes(receipt.id))

        receipt.move_ids[0].sudi_remarks = "chip on stone 4"
        self._sync_flush()

        changes = self._changes(receipt.id)
        self.assertEqual(len(changes), before + 1)
        self.assertEqual(changes[-1].op, "write")

    def test_a_timesheet_change_invalidates_its_picking(self):
        receipt = self._assigned_receipt()
        self._sync_flush()
        before = len(self._changes(receipt.id))

        employee = self.env["hr.employee"].sudo().create({
            "name": self.operator.name,
            "user_id": self.operator.id,
            "company_id": self.env.company.id,
        })
        self.env["account.analytic.line"].create({
            "name": "polishing",
            "sudi_picking_id": receipt.id,
            "sudi_job_type_id": self.job_type.id,
            "employee_id": employee.id,
            "project_id": self.env["account.analytic.line"]._sudi_get_timesheet_project().id,
            "date": fields.Date.context_today(receipt),
            "unit_amount": 1.5,
        })
        self._sync_flush()

        self.assertEqual(len(self._changes(receipt.id)), before + 1)

    def test_a_picking_that_is_not_job_work_is_not_logged(self):
        picking = self.env["stock.picking"].create({
            "partner_id": self.partner.id,
            "picking_type_id": self.picking_type_in.id,
            "location_id": self.supplier_location.id,
            "location_dest_id": self.stock_location.id,
            "sudi_is_diamond_job_work": False,
        })
        self._sync_flush()
        self.assertFalse(self._changes(picking.id))

    def test_the_skip_context_suppresses_logging(self):
        receipt = self._pending_receipt()
        self._sync_flush()
        before = len(self._changes(receipt.id))
        receipt.with_context(sudi_sync_skip=True).write({"sudi_internal_notes": "quiet"})
        self._sync_flush()
        self.assertEqual(len(self._changes(receipt.id)), before)

    def test_an_unlink_is_logged_as_such(self):
        receipt = self._pending_receipt()
        self._sync_flush()
        receipt_id = receipt.id
        receipt.unlink()
        self._sync_flush()
        self.assertEqual(self._changes(receipt_id)[-1].op, "unlink")


class TestSudiSyncCursor(SudiSyncCase):
    """The cursor, the lag that makes it safe, and retention."""

    def test_the_lag_withholds_a_fresh_row(self):
        # The real protection: a serial is handed out before commit, so a row
        # must settle before any device is told its id.
        self._set_param(PARAM_LAG_SECONDS, "30")
        self._pending_receipt()
        self._sync_flush()
        self.assertEqual(self.Change.sudo()._sudi_latest_visible_id(), 0)

    def test_a_settled_row_becomes_visible(self):
        self._set_param(PARAM_LAG_SECONDS, "30")
        receipt = self._pending_receipt()
        self._sync_flush()
        change = self._changes(receipt.id)
        # Backdate it rather than sleep: the query is what is under test.
        change.sudo().write({
            "logged_at": fields.Datetime.subtract(fields.Datetime.now(), minutes=1)
        })
        self.assertEqual(self.Change.sudo()._sudi_latest_visible_id(), change.id)

    def test_an_absent_cursor_is_stale(self):
        self.assertTrue(self.Change.sudo()._sudi_is_cursor_stale(None))
        self.assertTrue(self.Change.sudo()._sudi_is_cursor_stale(0))

    def test_an_empty_log_does_not_make_a_cursor_stale(self):
        # "Nothing has changed lately" must not force every phone to resync,
        # which is why staleness is measured against what was pruned and not
        # against MIN(id).
        self.assertFalse(self.Change.sudo()._sudi_is_cursor_stale(10_000))

    def test_a_cursor_below_the_pruned_watermark_is_stale(self):
        self._set_param(PARAM_PRUNED_THROUGH, "500")
        self.assertTrue(self.Change.sudo()._sudi_is_cursor_stale(499))
        self.assertFalse(self.Change.sudo()._sudi_is_cursor_stale(500))

    def test_changed_ids_are_returned_oldest_change_first(self):
        first = self._pending_receipt()
        second = self._pending_receipt()
        self._sync_flush()
        first.sudi_internal_notes = "touched again, so now the newest"
        self._sync_flush()

        rows = self.Change.sudo()._sudi_changed_res_ids(
            "stock.picking", 0, self.Change.sudo()._sudi_latest_visible_id()
        )
        self.assertEqual([res_id for res_id, _rev in rows], [second.id, first.id])

    def test_pruning_deletes_and_records_how_far_it_got(self):
        receipt = self._pending_receipt()
        self._sync_flush()
        change = self._changes(receipt.id)
        change.sudo().write({
            "logged_at": fields.Datetime.subtract(fields.Datetime.now(), days=60)
        })

        deleted = self.Change.sudo()._cron_prune()

        self.assertTrue(deleted)
        self.assertFalse(change.exists())
        self.assertEqual(self.Change.sudo()._sudi_pruned_through(), change.id)
        self.assertTrue(self.Change.sudo()._sudi_is_cursor_stale(change.id - 1))

    def test_pruning_leaves_rows_inside_the_window(self):
        receipt = self._pending_receipt()
        self._sync_flush()
        change = self._changes(receipt.id)
        change.sudo().write({
            "logged_at": fields.Datetime.now() - timedelta(days=2)
        })
        self.assertEqual(self.Change.sudo()._cron_prune(), 0)
        self.assertTrue(change.exists())

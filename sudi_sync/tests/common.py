from odoo import Command, fields

from odoo.addons.diamond.tests.test_diamond_job_work import SudiJobWorkCase

from ..models.sudi_sync_change import (
    PARAM_LAG_SECONDS,
    PARAM_PRUNED_THROUGH,
    PARAM_RETENTION_DAYS,
)


class SudiSyncCase(SudiJobWorkCase):
    """Fixtures for the sync engine.

    Two things every test here has to deal with:

    * **the visibility lag.** In production a change is withheld for a couple of
      seconds so a late commit cannot be read past. A test that waited for that
      would be a slow test, so the lag is switched off by default and one test
      switches it back on to prove it works.
    * **precommit.** Log rows are written by a ``cr.precommit`` callback, and a
      ``TransactionCase`` never commits, so nothing is written unless the test
      asks. ``_sync_flush`` is that ask, and it is also a faithful simulation:
      it is exactly what ``Cursor.commit`` does.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.operator = cls.env["res.users"].create({
            "name": "Ramesh Onfield",
            "login": "sudi_sync_operator",
            "group_ids": [Command.set([
                cls.env.ref("diamond.group_sudi_pickup_delivery_operator").id,
                cls.env.ref("diamond.group_sudi_job_work_user").id,
            ])],
        })
        cls.operator_2 = cls.env["res.users"].create({
            "name": "Suresh Onfield",
            "login": "sudi_sync_operator_2",
            "group_ids": [Command.set([
                cls.env.ref("diamond.group_sudi_pickup_delivery_operator").id,
            ])],
        })

    def setUp(self):
        super().setUp()
        self.Change = self.env["sudi.sync.change"]
        self._set_param(PARAM_LAG_SECONDS, "0")
        # Proof of delivery is mandatory by default; the sync engine is what is
        # under test here, not the handover.
        for requirement in ("receiver_name", "signature", "photo"):
            self._set_param(f"sudi_diamond.pod_require_{requirement}", "0")
        self._set_param(PARAM_RETENTION_DAYS, "30")
        self._set_param(PARAM_PRUNED_THROUGH, "0")
        # Dispatch notifications go out over WhatsApp; the engine is not what
        # is under test here.
        Picking = type(self.env["stock.picking"])
        for name in ("_sudi_notify_delivery_assigned", "_sudi_notify_pickup_confirmed"):
            original = getattr(Picking, name)
            setattr(Picking, name, lambda records: None)
            self.addCleanup(setattr, Picking, name, original)

    def _set_param(self, key, value):
        self.env["ir.config_parameter"].sudo().set_param(key, value)

    def _sync_flush(self):
        """Commit-time behaviour, without committing."""
        self.env.flush_all()
        self.env.cr.precommit.run()

    def _changes(self, res_id=None):
        domain = [("model", "=", "stock.picking")]
        if res_id is not None:
            domain.append(("res_id", "=", res_id))
        return self.Change.sudo().search(domain, order="id")

    def _pending_receipt(self, contact="9876543210"):
        """A receipt sitting in "Pick up pending", as a jangad upload leaves it."""
        return self.env["stock.picking"].create({
            "partner_id": self.partner.id,
            "picking_type_id": self.picking_type_in.id,
            "location_id": self.supplier_location.id,
            "location_dest_id": self.stock_location.id,
            "sudi_is_diamond_job_work": True,
            "sudi_customer_contact": contact,
            "sudi_pickup_address": "12, Mahidharpura, Surat",
        })

    def _assigned_receipt(self):
        """A receipt picked up and now in job work."""
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

    def _pull(self, user=None, **kwargs):
        return (
            self.env["stock.picking"]
            .with_user(user or self.operator)
            ._sudi_sync_pull(**kwargs)
        )

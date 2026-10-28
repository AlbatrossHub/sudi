from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import SQL


class AccountAnalyticLine(models.Model):
    # Odoo 20: timesheet_grid no longer mixes timer.mixin into
    # account.analytic.line, but the diamond receipt timer flow
    # (timer.parent.mixin on stock.picking -> record._create_timer() /
    # record.action_timer_stop() on the timesheet line) requires it, so the
    # module restores the mixin on the line itself.
    _name = "account.analytic.line"
    _inherit = ["account.analytic.line", "timer.mixin"]

    sudi_picking_id = fields.Many2one(
        "stock.picking",
        string="Receipt",
        ondelete="restrict",
        index=True,
        domain=[
            ("sudi_is_diamond_job_work", "=", True),
            ("picking_type_code", "=", "incoming"),
        ],
    )
    sudi_job_type_id = fields.Many2one(
        "sudi.diamond.job.type",
        string="Job Type",
        ondelete="restrict",
        index=True,
    )
    sudi_picking_partner_id = fields.Many2one(
        "res.partner",
        string="Receipt Customer",
        related="sudi_picking_id.partner_id",
        store=True,
        readonly=True,
    )
    sudi_picking_state = fields.Selection(
        related="sudi_picking_id.state",
        string="Receipt Status",
        store=True,
        readonly=True,
    )

    @api.model
    def _sudi_get_timesheet_project(self):
        project = self.env.ref("diamond.sudi_diamond_timesheet_project", raise_if_not_found=False)
        if not project:
            project = self.env["project.project"].search([
                ("name", "=", "Diamond Job Work Timesheets"),
                ("allow_timesheets", "=", True),
            ], limit=1)
        if not project:
            raise ValidationError(_("Please configure the Diamond Job Work Timesheets project."))
        return project

    @api.model
    def _sudi_prepare_receipt_timesheet_vals(self, vals):
        picking_id = vals.get("sudi_picking_id") or self.env.context.get("default_sudi_picking_id")
        if not picking_id:
            return vals

        picking = self.env["stock.picking"].browse(picking_id)
        if not picking.sudi_is_diamond_job_work or picking.picking_type_code != "incoming":
            raise ValidationError(_("Timesheets can only be linked to diamond job-work receipts."))

        project = self._sudi_get_timesheet_project()
        vals.setdefault("project_id", project.id)
        vals.setdefault("company_id", picking.company_id.id or project.company_id.id or self.env.company.id)
        vals.setdefault("partner_id", picking.partner_id.id)
        vals.setdefault("sudi_picking_id", picking.id)
        vals.setdefault("task_id", False)
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [
            self._sudi_prepare_receipt_timesheet_vals(vals.copy())
            for vals in vals_list
        ]
        return super().create(vals_list)

    def write(self, vals):
        if "sudi_picking_id" in vals and vals.get("sudi_picking_id"):
            vals = self._sudi_prepare_receipt_timesheet_vals(vals.copy())
        return super().write(vals)

    @api.constrains("sudi_picking_id", "sudi_job_type_id", "task_id")
    def _check_sudi_receipt_job_type(self):
        for line in self.filtered("sudi_picking_id"):
            if not line.sudi_job_type_id:
                raise ValidationError(_("A job type is required on receipt timesheet lines."))
            if line.task_id:
                raise ValidationError(_("Receipt timesheet lines cannot also be linked to a project task."))


class TimesheetsAnalysisReport(models.Model):
    """Mirror the receipt/job-type columns onto the Timesheets Reporting model.

    The shared timesheet search view (hr_timesheet_line_search_sudi_diamond) adds
    sudi_picking_id / sudi_job_type_id, and the EE Reporting screens reuse that view
    on this SQL-view model — without these columns the three "Timesheets/Reporting"
    screens crash on an unknown field (they did on v19 production as well).
    """

    _inherit = "timesheets.analysis.report"

    sudi_picking_id = fields.Many2one("stock.picking", string="Job Work Receipt", readonly=True)
    sudi_job_type_id = fields.Many2one("sudi.diamond.job.type", string="Diamond Job Type", readonly=True)

    @api.model
    def _select(self):
        return SQL(
            "%s, A.sudi_picking_id AS sudi_picking_id, A.sudi_job_type_id AS sudi_job_type_id",
            super()._select(),
        )

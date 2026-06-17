import re

from markupsafe import Markup, escape

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.float_utils import float_is_zero


class StockPicking(models.Model):
    _inherit = ["stock.picking", "timer.parent.mixin"]

    state = fields.Selection(selection_add=[
        ("sudi_pickup_pending", "Pick up pending"),
        ("draft",),
        ("assigned", "Job Work in Progress"),
    ])
    sudi_is_diamond_job_work = fields.Boolean(
        string="Diamond Job Work",
        copy=True,
        tracking=True,
        default=True,
    )
    sudi_origin_receipt_id = fields.Many2one(
        "stock.picking",
        string="Origin Receipt",
        copy=True,
        index=True,
    )
    sudi_delivery_ids = fields.One2many(
        "stock.picking",
        "sudi_origin_receipt_id",
        string="Diamond Deliveries",
    )
    sudi_origin_receipt_name = fields.Char(
        string="Origin Receipt",
        compute="_compute_sudi_origin_receipt_name",
        compute_sudo=True,
    )
    sudi_delivery_count = fields.Integer(compute="_compute_sudi_counts")
    sudi_invoice_ids = fields.Many2many(
        "account.move",
        compute="_compute_sudi_invoice_ids",
        string="Diamond Invoices",
    )
    sudi_invoice_count = fields.Integer(compute="_compute_sudi_invoice_ids")
    sudi_pickup_user_id = fields.Many2one(
        "res.users",
        string="Pickup Person",
        tracking=True,
    )
    sudi_pickup_datetime = fields.Datetime(string="Pickup Date/Time", tracking=True)
    sudi_customer_contact = fields.Char(string="Customer Contact", tracking=True)
    sudi_pickup_address_id = fields.Many2one(
        "res.partner",
        string="Pickup Address",
        copy=False,
        tracking=True,
    )
    sudi_pickup_address = fields.Text(
        string="Pickup Address",
        copy=False,
        tracking=True,
    )
    sudi_internal_notes = fields.Text(string="Job Work Notes", tracking=True)
    sudi_jangad_image = fields.Image(string="Jangad")
    sudi_partner_address = fields.Text(
        string="Customer Address",
        compute="_compute_sudi_partner_address",
    )
    sudi_billing_line_ids = fields.One2many(
        "sudi.diamond.billing.line",
        "picking_id",
        string="Billing Details",
        copy=False,
    )
    sudi_timesheet_ids = fields.One2many(
        "account.analytic.line",
        "sudi_picking_id",
        string="Timesheets",
    )
    sudi_total_hours_spent = fields.Float(
        string="Time Spent",
        compute="_compute_sudi_total_hours_spent",
        compute_sudo=True,
    )
    sudi_display_timesheet_timer = fields.Boolean(
        string="Display Timesheet Timer",
        compute="_compute_sudi_display_timesheet_timer",
    )
    sudi_timesheet_unit_amount = fields.Float(compute="_compute_sudi_timesheet_unit_amount")
    sudi_timesheet_job_type_id = fields.Many2one(
        "sudi.diamond.job.type",
        string="Timer Job Type",
        copy=False,
    )

    @api.model_create_multi
    def create(self, vals_list):
        pickings = super().create(vals_list)
        pickings._sudi_notify_pickup_scheduled()
        return pickings

    @api.model
    def _sudi_get_pickup_notify_user(self):
        param = self.env["ir.config_parameter"].sudo().get_param(
            "diamond.sudi_pickup_notify_user_id", "10"
        )
        try:
            user_id = int(param)
        except (TypeError, ValueError):
            return self.env["res.users"]
        user = self.env["res.users"].browse(user_id).exists()
        if not user.active:
            return self.env["res.users"]
        return user

    def _sudi_notify_pickup_scheduled(self):
        notify_user = self._sudi_get_pickup_notify_user()
        if not notify_user:
            return
        receipts = self.filtered(
            lambda picking: picking.sudi_is_diamond_job_work
            and picking.picking_type_code == "incoming"
            and picking.state == "sudi_pickup_pending"
        )
        for receipt in receipts:
            partner = notify_user.partner_id
            mention_html = Markup(
                "<a href=\"#\" class=\"o_mail_redirect\" data-oe-model=\"res.partner\" "
                "data-oe-id=\"%s\" target=\"_blank\" contenteditable=\"false\">@%s</a>"
            ) % (partner.id, escape(partner.display_name))
            body = Markup("%s %s") % (
                escape(_("A pick has been scheduled for receipt %s.", receipt.name)),
                mention_html,
            )
            receipt.sudo().message_post(
                body=body,
                subject=_("Pickup scheduled: %s") % receipt.name,
                message_type="comment",
                partner_ids=partner.ids,
                subtype_xmlid="mail.mt_comment",
            )

    @api.depends("sudi_delivery_ids")
    def _compute_sudi_counts(self):
        for picking in self:
            picking.sudi_delivery_count = len(picking.sudi_delivery_ids)

    @api.depends("sudi_origin_receipt_id.name")
    def _compute_sudi_origin_receipt_name(self):
        for picking in self:
            picking.sudi_origin_receipt_name = picking.sudi_origin_receipt_id.name or ""

    def _compute_sudi_invoice_ids(self):
        AccountMove = self.env["account.move"]
        for picking in self:
            if picking.picking_type_code == "incoming":
                invoices = AccountMove.search([("sudi_receipt_id", "=", picking.id)])
            else:
                invoices = AccountMove.search([("sudi_delivery_ids", "in", picking.ids)])
            picking.sudi_invoice_ids = invoices
            picking.sudi_invoice_count = len(invoices)

    @api.depends("partner_id")
    def _compute_sudi_partner_address(self):
        for picking in self:
            picking.sudi_partner_address = picking.partner_id.contact_address or ""

    @api.depends(
        "move_type",
        "move_ids.state",
        "move_ids.picking_id",
        "sudi_is_diamond_job_work",
        "sudi_pickup_datetime",
        "picking_type_id.code",
    )
    def _compute_state(self):
        super()._compute_state()
        for picking in self:
            if (
                picking.sudi_is_diamond_job_work
                and picking.picking_type_code == "incoming"
                and picking.state == "draft"
                and not picking.sudi_pickup_datetime
            ):
                picking.state = "sudi_pickup_pending"

    @api.depends("sudi_timesheet_ids.unit_amount")
    def _compute_sudi_total_hours_spent(self):
        if not any(self._ids):
            for picking in self:
                picking.sudi_total_hours_spent = sum(picking.sudi_timesheet_ids.mapped("unit_amount"))
            return

        timesheet_read_group = self.env["account.analytic.line"]._read_group(
            [("sudi_picking_id", "in", self.ids)],
            ["sudi_picking_id"],
            ["unit_amount:sum"],
        )
        hours_by_picking = {picking.id: unit_amount_sum for picking, unit_amount_sum in timesheet_read_group}
        for picking in self:
            picking.sudi_total_hours_spent = hours_by_picking.get(picking.id, 0.0)

    def _compute_sudi_display_timesheet_timer(self):
        uom_hour = self.env.ref("uom.product_uom_hour", raise_if_not_found=False)
        is_hour_encoding = self.env.company.timesheet_encode_uom_id == uom_hour
        for picking in self:
            picking.sudi_display_timesheet_timer = (
                is_hour_encoding
                and picking.sudi_is_diamond_job_work
                and picking.picking_type_code == "incoming"
                and picking.state == "assigned"
            )

    @api.depends("user_timer_id")
    def _compute_sudi_timesheet_unit_amount(self):
        timesheet_ids = self.mapped("user_timer_id.res_id")
        unit_amount_by_timesheet_id = {}
        if timesheet_ids:
            timesheet_read = self.env["account.analytic.line"].search_read(
                [("id", "in", timesheet_ids)],
                ["unit_amount"],
            )
            unit_amount_by_timesheet_id = {
                timesheet["id"]: timesheet["unit_amount"]
                for timesheet in timesheet_read
            }

        for picking in self:
            timesheet_id = picking.user_timer_id.res_id if picking.user_timer_id else False
            picking.sudi_timesheet_unit_amount = unit_amount_by_timesheet_id.get(timesheet_id, 0.0)

    def action_sudi_confirm_pickup(self):
        self._sudi_check_pickup_delivery_operator_access()
        invalid_pickings = self.filtered(
            lambda picking: not picking.sudi_is_diamond_job_work
            or picking.picking_type_code != "incoming"
            or picking.state != "sudi_pickup_pending"
        )
        if invalid_pickings:
            raise UserError(_("Pickup can only be confirmed on diamond job-work receipts waiting for pickup."))

        self.write({
            "sudi_pickup_user_id": self.env.user.id,
            "sudi_pickup_datetime": fields.Datetime.now(),
        })
        return True

    def _sudi_check_pickup_delivery_operator_access(self):
        if self.env.su:
            return
        if not self.env.user.has_group("base.group_user"):
            raise AccessError(_("You are not allowed to operate diamond pickup and delivery records."))

    def action_timer_start(self):
        self.ensure_one()
        if self.sudi_display_timesheet_timer:
            return super().action_timer_start()
        return False

    def action_timer_stop(self):
        self.ensure_one()
        if self.sudi_display_timesheet_timer and self.user_timer_id:
            timesheet = self._get_record_with_timer_running()
            if timesheet:
                return {
                    "name": _("Confirm Time Spent"),
                    "type": "ir.actions.act_window",
                    "res_model": "hr.timesheet.stop.timer.confirmation.wizard",
                    "context": {
                        "default_timesheet_id": timesheet.id,
                        "dialog_size": "medium",
                    },
                    "views": [[
                        self.env.ref("timesheet_grid.hr_timesheet_stop_timer_confirmation_wizard_view_form").id,
                        "form",
                    ]],
                    "target": "new",
                }
            return super().action_timer_stop()
        return False

    def _sudi_get_default_timesheet_job_type(self):
        self.ensure_one()
        if self.sudi_timesheet_job_type_id:
            return self.sudi_timesheet_job_type_id

        job_types = self.move_ids.filtered(
            lambda move: move.state != "cancel" and move.sudi_job_type_id
        ).mapped("sudi_job_type_id")
        return job_types if len(job_types) == 1 else self.env["sudi.diamond.job.type"]

    def _create_record_to_start_timer(self):
        self.ensure_one()
        job_type = self._sudi_get_default_timesheet_job_type()
        if not job_type:
            raise UserError(_("Please select a Timer Job Type before starting the receipt timer."))

        project = self.env["account.analytic.line"]._sudi_get_timesheet_project()
        return self.env["account.analytic.line"].create({
            "sudi_picking_id": self.id,
            "sudi_job_type_id": job_type.id,
            "project_id": project.id,
            "date": fields.Date.context_today(self),
            "name": "/",
            "user_id": self.env.uid,
        })

    def _action_interrupt_user_timers(self):
        self.action_timer_stop()

    @api.model
    def _sudi_normalize_phone(self, phone):
        digits = re.sub(r"\D+", "", phone or "")
        return digits[-10:] if len(digits) > 10 else digits

    @api.model
    def _sudi_find_partner_by_phone(self, phone):
        phone_key = self._sudi_normalize_phone(phone)
        if not phone_key:
            return self.env["res.partner"]

        Partner = self.env["res.partner"].sudo()
        phone_fields = [field for field in ("phone", "mobile") if field in Partner._fields]
        if not phone_fields:
            return self.env["res.partner"]

        domain = [(phone_fields[0], "!=", False)]
        for field in phone_fields[1:]:
            domain = ["|", (field, "!=", False)] + domain

        partners = Partner.search(domain)
        for partner in partners:
            partner_numbers = {
                self._sudi_normalize_phone(partner[field])
                for field in phone_fields
                if partner[field]
            }
            if phone_key in partner_numbers:
                return partner.commercial_partner_id
        return self.env["res.partner"]

    @api.model
    def _sudi_format_pickup_address(self, partner):
        address = partner.contact_address or ""
        return "\n".join(line.strip() for line in address.splitlines() if line.strip())

    @api.model
    def sudi_get_public_pickup_address_suggestions(self, phone=None, partner=None):
        commercial_partner = (partner or self._sudi_find_partner_by_phone(phone)).sudo().commercial_partner_id
        if not commercial_partner:
            return []

        candidates = commercial_partner | commercial_partner.child_ids.filtered("active")
        suggestions = []
        seen_partner_ids = set()
        for candidate in candidates:
            if candidate.id in seen_partner_ids:
                continue
            address = self._sudi_format_pickup_address(candidate)
            if not address:
                continue
            seen_partner_ids.add(candidate.id)
            suggestions.append({
                "id": candidate.id,
                "name": candidate.display_name,
                "address": address,
                "is_default": candidate == commercial_partner,
            })
        return suggestions

    @api.model
    def _sudi_get_or_create_public_jangad_partner(self, phone):
        partner = self._sudi_find_partner_by_phone(phone)
        if partner:
            return partner
        return self.env["res.partner"].sudo().create({
            "name": _("Jangad Customer %s") % (phone or "").strip(),
            "phone": (phone or "").strip(),
        })

    @api.model
    def _sudi_create_manual_pickup_address(self, commercial_partner, phone, manual_pickup_address):
        lines = [
            line.strip()
            for line in (manual_pickup_address or "").splitlines()
            if line.strip()
        ]
        if not lines:
            return self.env["res.partner"]

        return self.env["res.partner"].sudo().create({
            "parent_id": commercial_partner.id,
            "type": "delivery",
            "name": _("Pickup Address"),
            "phone": (phone or "").strip(),
            "street": lines[0],
            "street2": "\n".join(lines[1:]),
        })

    @api.model
    def _sudi_resolve_public_pickup_address(self, phone, pickup_address_id=False, manual_pickup_address=False):
        Partner = self.env["res.partner"].sudo()
        manual_pickup_address = (manual_pickup_address or "").strip()
        commercial_partner = self._sudi_find_partner_by_phone(phone)
        pickup_address = self.env["res.partner"]

        if pickup_address_id:
            try:
                pickup_address_id = int(pickup_address_id)
            except (TypeError, ValueError):
                raise UserError(_("Please select a valid pickup address."))
            pickup_address = Partner.browse(pickup_address_id).exists()
            if not pickup_address:
                raise UserError(_("Please select a valid pickup address."))
            commercial_partner = commercial_partner or pickup_address.commercial_partner_id
            valid_address_ids = {
                suggestion["id"]
                for suggestion in self.sudi_get_public_pickup_address_suggestions(phone, commercial_partner)
            }
            if pickup_address.id not in valid_address_ids:
                raise UserError(_("The selected pickup address does not match the entered phone number."))
        elif manual_pickup_address:
            commercial_partner = self._sudi_get_or_create_public_jangad_partner(phone)
            pickup_address = self._sudi_create_manual_pickup_address(
                commercial_partner,
                phone,
                manual_pickup_address,
            )
        else:
            suggestions = self.sudi_get_public_pickup_address_suggestions(phone, commercial_partner)
            if not suggestions:
                raise UserError(_("Please enter a pickup address."))
            pickup_address = Partner.browse(suggestions[0]["id"])

        pickup_address_text = manual_pickup_address or self._sudi_format_pickup_address(pickup_address)
        return commercial_partner, pickup_address, pickup_address_text

    @api.model
    def _sudi_get_public_receipt_defaults(self):
        company = self.env.company
        warehouse = self.env["stock.warehouse"].sudo().search([("company_id", "=", company.id)], limit=1)
        if not warehouse:
            warehouse = self.env["stock.warehouse"].sudo().search([], limit=1)

        picking_type = warehouse.in_type_id if warehouse else self.env["stock.picking.type"]
        if not picking_type:
            picking_type = self.env["stock.picking.type"].sudo().search([
                ("code", "=", "incoming"),
                ("company_id", "in", [False, company.id]),
            ], limit=1)
        if not picking_type:
            raise UserError(_("No incoming receipt operation type is configured."))

        source_location = (
            picking_type.default_location_src_id
            or self.env.ref("stock.stock_location_suppliers", raise_if_not_found=False)
        )
        destination_location = picking_type.default_location_dest_id or warehouse.lot_stock_id
        if not source_location or not destination_location:
            raise UserError(_("Please configure source and destination locations on the receipt operation type."))
        return picking_type, source_location, destination_location

    @api.model
    def sudi_create_public_jangad_receipt(
        self,
        phone,
        jangad_image,
        pickup_address_id=False,
        manual_pickup_address=False,
    ):
        picking_type, source_location, destination_location = self._sudi_get_public_receipt_defaults()
        partner, pickup_address, pickup_address_text = self._sudi_resolve_public_pickup_address(
            phone,
            pickup_address_id=pickup_address_id,
            manual_pickup_address=manual_pickup_address,
        )
        vals = {
            "picking_type_id": picking_type.id,
            "location_id": source_location.id,
            "location_dest_id": destination_location.id,
            "company_id": picking_type.company_id.id or self.env.company.id,
            "sudi_is_diamond_job_work": True,
            "sudi_customer_contact": phone,
            "sudi_pickup_address": pickup_address_text,
            "sudi_jangad_image": jangad_image,
        }
        if partner:
            vals["partner_id"] = partner.id
        if pickup_address:
            vals["sudi_pickup_address_id"] = pickup_address.id
        return self.sudo().create(vals)

    @api.onchange(
        "partner_id",
        "company_id",
        "move_ids",
        "move_ids.sudi_job_type_id",
        "move_ids.sudi_pcs_qty",
        "move_ids.sudi_carats",
        "move_ids.quantity",
        "move_ids.product_uom_qty",
    )
    def _onchange_sudi_billing_details_source(self):
        for picking in self.filtered(
            lambda record: record.sudi_is_diamond_job_work and record.picking_type_code == "incoming" and not record.id
        ):
            picking.sudi_billing_line_ids = [
                Command.clear(),
                *[
                    Command.create(vals)
                    for vals in picking._sudi_prepare_billing_detail_values()
                ],
            ]

    def action_sudi_recompute_billing_details(self):
        self._sudi_sync_billing_details()
        return True

    def _sudi_prepare_billing_detail_values(self):
        self.ensure_one()
        grouped = {}
        for index, move in enumerate(
            self.move_ids.filtered(lambda stock_move: stock_move.state != "cancel" and stock_move.sudi_job_type_id),
            start=1,
        ):
            quantity = move._sudi_get_invoice_quantity()
            rounding = move.product_uom.rounding if move.product_uom else 0.01
            if float_is_zero(quantity, precision_rounding=rounding):
                continue
            job_type = move.sudi_job_type_id
            group = grouped.setdefault(
                job_type.id,
                {
                    "job_type": job_type,
                    "quantity": 0.0,
                    "sequence": move.sudi_sr or index,
                },
            )
            group["quantity"] += quantity
            if move.sudi_sr:
                group["sequence"] = min(group["sequence"], move.sudi_sr)

        values = []
        partner = self.partner_id.commercial_partner_id
        for group in sorted(grouped.values(), key=lambda item: (item["sequence"], item["job_type"].display_name)):
            job_type = group["job_type"]
            price_unit, price_source = job_type._sudi_get_price_for_partner_with_source(partner, self.company_id)
            values.append({
                "sequence": group["sequence"],
                "job_type_id": job_type.id,
                "name": job_type.invoice_description or job_type.display_name,
                "quantity": group["quantity"],
                "price_unit": price_unit,
                "price_source": price_source,
                "active": True,
            })
        return values

    def _sudi_sync_billing_details(self):
        BillingLine = self.env["sudi.diamond.billing.line"].sudo()
        for receipt in self.filtered(
            lambda picking: picking.sudi_is_diamond_job_work and picking.picking_type_code == "incoming"
        ):
            prepared_values = receipt._sudi_prepare_billing_detail_values()
            prepared_job_type_ids = {vals["job_type_id"] for vals in prepared_values}
            existing_lines = receipt.sudi_billing_line_ids.sudo().filtered(lambda line: line.active and not line.invoice_line_id)
            existing_by_job_type = {line.job_type_id.id: line for line in existing_lines}

            for vals in prepared_values:
                line = existing_by_job_type.get(vals["job_type_id"])
                if not line:
                    BillingLine.create({"picking_id": receipt.id, **vals})
                    continue

                write_vals = {
                    "sequence": vals["sequence"],
                    "name": vals["name"],
                    "active": True,
                }
                if not line.manual_quantity:
                    write_vals["quantity"] = vals["quantity"]
                if line.manual_price:
                    write_vals["price_source"] = "manual"
                else:
                    write_vals["price_unit"] = vals["price_unit"]
                    write_vals["price_source"] = vals["price_source"]
                line.write(write_vals)

            stale_lines = existing_lines.filtered(
                lambda line: line.job_type_id.id not in prepared_job_type_ids
                and not line.manual_quantity
                and not line.manual_price
            )
            stale_lines.write({"active": False})

    def _autoconfirm_picking(self):
        regular_pickings = self.filtered(
            lambda picking: not (
                picking.sudi_is_diamond_job_work
                and picking.picking_type_code == "incoming"
                and (not picking.sudi_pickup_datetime or picking.state in ("sudi_pickup_pending", "draft"))
            )
        )
        return super(StockPicking, regular_pickings)._autoconfirm_picking()

    def _pre_action_done_hook(self):
        res = super()._pre_action_done_hook()
        if res is not True:
            return res
        self._sudi_validate_job_work_pickings()
        return True

    def _action_done(self):
        res = super()._action_done()
        diamond_receipts = self.filtered(
            lambda picking: picking.sudi_is_diamond_job_work
            and picking.picking_type_id.code == "incoming"
            and picking.state == "done"
        )
        diamond_receipts._sudi_sync_billing_details()
        diamond_receipts.filtered(lambda picking: not picking.sudi_delivery_ids)._sudi_create_delivery_from_receipt()
        return res

    def _sudi_validate_job_work_pickings(self):
        for picking in self.filtered("sudi_is_diamond_job_work"):
            if not picking.partner_id:
                raise ValidationError(_("A diamond job-work transfer must have a customer."))
            for move in picking.move_ids.filtered(lambda m: m.state != "cancel"):
                if not move.product_id:
                    raise ValidationError(_("Every diamond job-work line must have a product."))
                if not move.sudi_job_type_id:
                    raise ValidationError(_("Every diamond job-work line must have a job type."))
                if move.sudi_pcs_qty < 0 or move.sudi_carats < 0:
                    raise ValidationError(_("Pieces/Qty and Carats must be zero or positive."))

    def _sudi_create_delivery_from_receipt(self):
        for receipt in self:
            delivery_type = receipt._sudi_get_delivery_picking_type()
            source_location = delivery_type.default_location_src_id or receipt.location_dest_id
            destination_location = (
                delivery_type.default_location_dest_id
                or self.env.ref("stock.stock_location_customers", raise_if_not_found=False)
            )
            if not source_location or not destination_location:
                raise UserError(_("Please configure source and destination locations on the delivery operation type."))

            move_commands = []
            for move in receipt.move_ids.filtered(lambda m: m.state == "done"):
                quantity = move.quantity or move.product_uom_qty
                if float_is_zero(quantity, precision_rounding=move.product_uom.rounding):
                    continue
                move_commands.append(Command.create({
                    "description_picking": move.description_picking or move.product_id.display_name,
                    "product_id": move.product_id.id,
                    "product_uom_qty": quantity,
                    "product_uom": move.product_uom.id,
                    "location_id": source_location.id,
                    "location_dest_id": destination_location.id,
                    "partner_id": receipt.partner_id.id,
                    "company_id": receipt.company_id.id,
                    "move_orig_ids": [Command.link(move.id)],
                    "sudi_sr": move.sudi_sr,
                    "sudi_size": move.sudi_size,
                    "sudi_pcs_qty": move.sudi_pcs_qty or quantity,
                    "sudi_carats": move.sudi_carats,
                    "sudi_job_type_id": move.sudi_job_type_id.id,
                    "sudi_remarks": move.sudi_remarks,
                    "sudi_origin_receipt_move_id": move.id,
                }))

            if not move_commands:
                continue

            delivery = self.create({
                "partner_id": receipt.partner_id.id,
                "picking_type_id": delivery_type.id,
                "location_id": source_location.id,
                "location_dest_id": destination_location.id,
                "origin": receipt.name,
                "company_id": receipt.company_id.id,
                "scheduled_date": fields.Datetime.now(),
                "sudi_is_diamond_job_work": True,
                "sudi_origin_receipt_id": receipt.id,
                "sudi_customer_contact": receipt.sudi_customer_contact,
                "sudi_internal_notes": receipt.sudi_internal_notes,
                "move_ids": move_commands,
            })
            delivery.action_confirm()
            delivery.action_assign()

    def action_sudi_mark_delivered(self):
        self._sudi_check_pickup_delivery_operator_access()
        invalid_pickings = self.filtered(
            lambda picking: not picking.sudi_is_diamond_job_work
            or picking.picking_type_code != "outgoing"
            or not picking.sudi_origin_receipt_id
            or picking.state in ("done", "cancel")
        )
        if invalid_pickings:
            raise UserError(_("Only active diamond job-work deliveries can be marked delivered."))

        self.write({
            "sudi_pickup_user_id": self.env.user.id,
            "sudi_pickup_datetime": fields.Datetime.now(),
        })
        for move in self.move_ids.filtered(lambda stock_move: stock_move.state not in ("done", "cancel")):
            if float_is_zero(move.quantity, precision_rounding=move.product_uom.rounding):
                move.quantity = move.product_uom_qty
            move.picked = True
        return self.button_validate()

    def _sudi_get_delivery_picking_type(self):
        self.ensure_one()
        warehouse = self.picking_type_id.warehouse_id or self.env["stock.warehouse"].search(
            [("company_id", "=", self.company_id.id)],
            limit=1,
        )
        delivery_type = warehouse.out_type_id if warehouse else self.env["stock.picking.type"]
        if not delivery_type:
            delivery_type = self.env["stock.picking.type"].search(
                [("code", "=", "outgoing"), ("company_id", "in", [False, self.company_id.id])],
                limit=1,
            )
        if not delivery_type:
            raise UserError(_("No outgoing delivery operation type is configured for this company."))
        return delivery_type

    def action_sudi_view_deliveries(self):
        self.ensure_one()
        return self._sudi_action_view_pickings(self.sudi_delivery_ids, _("Diamond Deliveries"))

    def action_sudi_view_origin_receipt(self):
        self.ensure_one()
        return self._sudi_action_view_pickings(self.sudi_origin_receipt_id, _("Origin Receipt"))

    def action_sudi_view_invoices(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id("account.action_move_out_invoice")
        invoices = self.sudi_invoice_ids
        action["domain"] = [("id", "in", invoices.ids)]
        if len(invoices) == 1:
            action["views"] = [(False, "form")]
            action["res_id"] = invoices.id
        return action

    def action_sudi_create_invoice(self):
        self.ensure_one()
        receipt = self if self.picking_type_code == "incoming" else self.sudi_origin_receipt_id
        delivery_pickings = self.sudi_delivery_ids if self.picking_type_code == "incoming" else self
        delivery_pickings = delivery_pickings.filtered(
            lambda picking: picking.sudi_is_diamond_job_work
            and picking.picking_type_code == "outgoing"
            and picking.state == "done"
        )
        delivered_moves = delivery_pickings.move_ids.filtered(
            lambda move: move.state == "done"
            and move.sudi_job_type_id
        )
        if not delivered_moves:
            raise UserError(_("There are no delivered uninvoiced diamond job-work lines."))

        partner = (receipt or self).partner_id.commercial_partner_id
        self._sudi_validate_invoice_partner(partner, (receipt or self).partner_id)
        receipt._sudi_sync_billing_details()
        billing_lines = receipt.sudi_billing_line_ids.filtered(
            lambda line: line.active
            and not line.invoice_line_id
            and not float_is_zero(line.quantity, precision_rounding=line.service_product_id.uom_id.rounding)
        )
        if not billing_lines:
            raise UserError(_("There are no uninvoiced diamond billing lines."))

        invoice = self.env["account.move"].create({
            "move_type": "out_invoice",
            "partner_id": partner.id,
            "partner_shipping_id": (receipt or self).partner_id.id,
            "invoice_origin": receipt.name if receipt else ", ".join(delivery_pickings.mapped("name")),
            "ref": ", ".join(delivery_pickings.mapped("name")),
            "company_id": (receipt or self).company_id.id,
            "sudi_is_diamond_job_work_invoice": True,
            "sudi_receipt_id": receipt.id if receipt else False,
            "sudi_delivery_ids": [Command.set(delivery_pickings.ids)],
        })

        line_commands = []
        custom_tax_by_billing_line = {}
        for billing_line in billing_lines:
            job_type = billing_line.job_type_id
            product = billing_line.service_product_id
            if not product:
                raise UserError(_("Please configure a service product on job type %s.") % job_type.display_name)
            line_vals = {
                "product_id": product.id,
                "name": billing_line.name,
                "quantity": billing_line.quantity,
                "product_uom_id": product.uom_id.id,
                "price_unit": billing_line.price_unit,
                "sudi_billing_line_id": billing_line.id,
            }
            if job_type.tax_ids:
                custom_tax_by_billing_line[billing_line.id] = invoice.fiscal_position_id.map_tax(
                    job_type.tax_ids._filter_taxes_by_company(invoice.company_id)
                )
            line_commands.append(Command.create(line_vals))

        if not line_commands:
            invoice.unlink()
            raise UserError(_("The delivered diamond job-work lines have zero invoice quantity."))

        invoice.write({"invoice_line_ids": line_commands})
        invoice.action_update_fpos_values()
        for line in invoice.invoice_line_ids.filtered(lambda invoice_line: invoice_line.sudi_billing_line_id.id in custom_tax_by_billing_line):
            line.tax_ids = custom_tax_by_billing_line[line.sudi_billing_line_id.id]
        for line in invoice.invoice_line_ids.filtered("sudi_billing_line_id"):
            billing_line = line.sudi_billing_line_id.sudo()
            billing_line.invoice_line_id = line
            delivered_moves.filtered(
                lambda move: move.sudi_job_type_id == billing_line.job_type_id
            ).sudi_invoice_line_id = line
        return {
            "type": "ir.actions.act_window",
            "name": _("Diamond Job Work Invoice"),
            "res_model": "account.move",
            "res_id": invoice.id,
            "view_mode": "form",
            "target": "current",
        }

    def _sudi_validate_invoice_partner(self, partner, shipping_partner):
        self.ensure_one()
        company = self.company_id
        if company.country_code == "IN" and not company.state_id:
            raise UserError(_("Please configure a State on the company before creating an Indian GST invoice."))
        partner_to_check = shipping_partner or partner
        if (
            company.country_code == "IN"
            and (not partner_to_check.country_id or partner_to_check.country_id.code == "IN")
            and not partner_to_check.state_id
        ):
            raise UserError(_("Please configure a State on customer %s before creating an Indian GST invoice.") % partner_to_check.display_name)

    def _sudi_action_view_pickings(self, pickings, name):
        action = self.env["ir.actions.actions"]._for_xml_id("stock.action_picking_tree_all")
        action["name"] = name
        action["domain"] = [("id", "in", pickings.ids)]
        if len(pickings) == 1:
            action["views"] = [(False, "form")]
            action["res_id"] = pickings.id
        return action

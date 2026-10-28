from odoo import api, fields, models


class SudiDiamondServiceRequest(models.Model):
    """One line of what the customer asked for: a service, how much, a remark.

    Captured with the jangad, before anybody has counted anything, so it is a
    *request* and not an item line. The office turns it into a ``stock.move``
    once the parcel is in, and ``move_id`` remembers which one, so the same
    request is never entered twice.
    """

    _name = "sudi.diamond.service.request"
    _description = "Requested Service"
    _order = "picking_id, sequence, id"

    picking_id = fields.Many2one(
        "stock.picking",
        string="Receipt",
        required=True,
        ondelete="cascade",
        index=True,
    )
    sequence = fields.Integer(default=10)
    job_type_id = fields.Many2one(
        "sudi.diamond.job.type",
        string="Service",
        required=True,
        ondelete="restrict",
        domain=[("active", "=", True)],
    )
    quantity = fields.Float(
        digits="Product Unit",
        default=0.0,
        help="As the customer stated it, in the service's own unit.",
    )
    unit = fields.Char(
        compute="_compute_unit",
        help="pcs or ct, from how the service is billed.",
    )
    remarks = fields.Char()
    move_id = fields.Many2one(
        "stock.move",
        string="Item Line",
        ondelete="set null",
        readonly=True,
        copy=False,
        index="btree_not_null",
        help="The item line the office entered from this request. Removing "
             "that line makes the request open again.",
    )
    company_id = fields.Many2one(
        related="picking_id.company_id", store=True, index=True
    )

    _quantity_non_negative = models.Constraint(
        "CHECK(quantity >= 0)", "A requested quantity cannot be negative."
    )

    @api.depends("job_type_id.invoice_basis")
    def _compute_unit(self):
        for request in self:
            request.unit = request.job_type_id._sudi_unit() if request.job_type_id else ""

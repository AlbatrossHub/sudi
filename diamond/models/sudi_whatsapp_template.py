from odoo import fields, models


class SudiWhatsappTemplate(models.Model):
    _inherit = "sudi.whatsapp.template"

    event = fields.Selection(
        selection_add=[("invoice_posted", "Job Work Invoice (To Customer)")],
        ondelete={"invoice_posted": "cascade"},
    )

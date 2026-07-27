from odoo import models, fields, api

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    show_powered_by = fields.Boolean(
        string="Show 'Powered by Odoo' Footer Branding",
        config_parameter='hide_powered_by.show_powered_by',
        help="Uncheck this to hide the default 'Powered by Odoo' message in the website footer."
    )

    powered_by_html = fields.Html(
        string="Custom Footer Text (HTML)",
        help="Customize your website footer with rich HTML (bold, colors, links, etc.)."
    )

    @api.model
    def get_values(self):
        res = super().get_values()
        param = self.env['ir.config_parameter'].sudo()
        res.update({
            'powered_by_html': param.get_param('hide_powered_by.powered_by_html', default=''),
        })
        return res

    def set_values(self):
        super().set_values()
        param = self.env['ir.config_parameter'].sudo()
        param.set_param('hide_powered_by.powered_by_html', self.powered_by_html or '')


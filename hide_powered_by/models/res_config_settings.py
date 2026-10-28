from markupsafe import Markup

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
            'powered_by_html': param.get_str('hide_powered_by.powered_by_html'),
        })
        return res

    def set_values(self):
        super().set_values()
        param = self.env['ir.config_parameter'].sudo()
        param.set_str('hide_powered_by.powered_by_html', self.powered_by_html or '')


    @api.model
    def _get_powered_by_markup(self):
        """Stored footer HTML as Markup, so QWeb t-out renders it unescaped.

        The value is admin-configured (Settings form), the same trust level
        the old t-raw gave it.
        """
        return Markup(
            self.env['ir.config_parameter'].sudo().get_str('hide_powered_by.powered_by_html')
        )

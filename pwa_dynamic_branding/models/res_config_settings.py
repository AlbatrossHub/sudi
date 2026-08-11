# -*- coding: utf-8 -*-
# Part of Aura Odoo Tech. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    # ========================================
    # PWA Basic Settings
    # ========================================
    pwa_app_name = fields.Char(
        string='PWA App Name',
        config_parameter='pwa.app_name',
        default='Odoo',
    )
    pwa_app_short_name = fields.Char(
        string='PWA Short Name',
        config_parameter='pwa.app_short_name',
        default='Odoo',
    )
    pwa_app_description = fields.Char(
        string='PWA App Description',
        config_parameter='pwa.app_description',
    )

    # ========================================
    # PWA Icons
    # ========================================
    pwa_icon_192 = fields.Binary(
        string='PWA Icon 192x192',
        related='pwa_config_id.icon_192',
        readonly=False,
    )
    pwa_icon_512 = fields.Binary(
        string='PWA Icon 512x512',
        related='pwa_config_id.icon_512',
        readonly=False,
    )
    pwa_icon_maskable = fields.Binary(
        string='PWA Maskable Icon',
        related='pwa_config_id.icon_maskable',
        readonly=False,
    )
    pwa_apple_touch_icon = fields.Binary(
        string='Apple Touch Icon',
        related='pwa_config_id.apple_touch_icon',
        readonly=False,
    )

    # ========================================
    # PWA Colors
    # ========================================
    pwa_theme_color = fields.Char(
        string='Theme Color',
        config_parameter='pwa.theme_color',
        default='#714B67',
    )
    pwa_background_color = fields.Char(
        string='Background Color',
        config_parameter='pwa.background_color',
        default='#714B67',
    )
    pwa_primary_color = fields.Char(
        string='Primary Brand Color',
        config_parameter='pwa.primary_color',
        default='#714B67',
    )
    pwa_secondary_color = fields.Char(
        string='Secondary Color',
        config_parameter='pwa.secondary_color',
        default='#875A7B',
    )
    pwa_accent_color = fields.Char(
        string='Accent Color',
        config_parameter='pwa.accent_color',
        default='#00A09D',
    )

    # ========================================
    # PWA Splash / Branding
    # ========================================
    pwa_splash_image = fields.Binary(
        string='Splash Screen Image',
        related='pwa_config_id.splash_image',
        readonly=False,
    )
    pwa_splash_background_color = fields.Char(
        string='Splash Background',
        config_parameter='pwa.splash_background_color',
        default='#FFFFFF',
    )
    pwa_splash_tagline = fields.Char(
        string='Splash Tagline',
        config_parameter='pwa.splash_tagline',
    )
    pwa_show_splash_logo = fields.Boolean(
        string='Show Logo in Splash',
        config_parameter='pwa.show_splash_logo',
        default=True,
    )

    # ========================================
    # PWA Behavior
    # ========================================
    pwa_display_mode = fields.Selection([
        ('fullscreen', 'Fullscreen'),
        ('standalone', 'Standalone (Default)'),
        ('minimal-ui', 'Minimal UI'),
        ('browser', 'Browser'),
    ], string='Display Mode',
        config_parameter='pwa.display_mode',
        default='standalone',
    )
    pwa_orientation = fields.Selection([
        ('any', 'Any'),
        ('natural', 'Natural'),
        ('portrait', 'Portrait'),
        ('landscape', 'Landscape'),
    ], string='Screen Orientation',
        config_parameter='pwa.orientation',
        default='any',
    )
    pwa_start_url = fields.Char(
        string='Start URL',
        config_parameter='pwa.start_url',
        default='/odoo',
    )
    pwa_scope = fields.Char(
        string='PWA Scope',
        config_parameter='pwa.scope',
        default='/odoo',
    )

    # ========================================
    # Portal PWA Settings
    # ========================================
    pwa_enable_portal = fields.Boolean(
        string='Enable Portal PWA',
        config_parameter='pwa.enable_portal',
        default=False,
    )
    pwa_portal_app_name = fields.Char(
        string='Portal App Name',
        config_parameter='pwa.portal_app_name',
        default='Customer Portal',
    )
    pwa_portal_theme_color = fields.Char(
        string='Portal Theme Color',
        config_parameter='pwa.portal_theme_color',
        default='#00A09D',
    )
    pwa_portal_start_url = fields.Char(
        string='Portal Start URL',
        config_parameter='pwa.portal_start_url',
        default='/my',
    )

    # ========================================
    # Offline Settings
    # ========================================
    pwa_enable_offline = fields.Boolean(
        string='Enable Offline Mode',
        config_parameter='pwa.enable_offline',
        default=True,
    )
    pwa_offline_page_title = fields.Char(
        string='Offline Page Title',
        config_parameter='pwa.offline_page_title',
        default='You are offline',
    )
    pwa_offline_page_message = fields.Char(
        string='Offline Message',
        config_parameter='pwa.offline_page_message',
        default='Please check your internet connection and try again.',
    )

    # ========================================
    # AI Branding Settings
    # ========================================
    pwa_ai_enabled = fields.Boolean(
        string='Enable AI Branding',
        config_parameter='pwa.ai_enabled',
        default=False,
    )
    pwa_ai_brand_prompt = fields.Char(
        string='Brand Description for AI',
        config_parameter='pwa.ai_brand_prompt',
        help='Describe your brand (industry, values, target audience) for AI to generate suggestions',
    )

    # ========================================
    # Reference to PWA Config
    # ========================================
    pwa_config_id = fields.Many2one(
        'pwa.config',
        string='PWA Configuration',
        compute='_compute_pwa_config_id',
    )
    pwa_preview_html = fields.Html(
        string='PWA Preview',
        compute='_compute_pwa_preview',
    )

    @api.depends('company_id')
    def _compute_pwa_config_id(self):
        """Get or create PWA config for current company"""
        PWAConfig = self.env['pwa.config'].sudo()
        for record in self:
            config = PWAConfig.search([
                ('company_id', '=', record.company_id.id),
                ('active', '=', True)
            ], limit=1)
            
            if not config:
                # Create default config for company
                config = PWAConfig.create({
                    'company_id': record.company_id.id,
                    'app_name': record.company_id.name or 'Odoo',
                })
            
            record.pwa_config_id = config.id

    @api.depends('pwa_app_name', 'pwa_theme_color', 'pwa_background_color', 'pwa_icon_192')
    def _compute_pwa_preview(self):
        """Generate PWA preview HTML"""
        for record in self:
            icon_src = '/web/static/img/odoo-icon-192x192.png'
            if record.pwa_config_id and record.pwa_config_id.icon_192:
                icon_src = f'/web/image/pwa.config/{record.pwa_config_id.id}/icon_192'
            
            app_name = record.pwa_app_name or 'Odoo'
            theme_color = record.pwa_theme_color or '#714B67'
            bg_color = record.pwa_background_color or '#714B67'
            
            record.pwa_preview_html = f'''
                <div class="pwa-preview-wrapper" style="padding: 20px; background: linear-gradient(135deg, #f5f5f5 0%, #e0e0e0 100%); border-radius: 16px;">
                    <div style="display: flex; gap: 24px; align-items: flex-start; flex-wrap: wrap;">
                        <!-- Mobile Preview -->
                        <div style="text-align: center;">
                            <div style="font-size: 12px; color: #666; margin-bottom: 8px;">📱 Mobile App</div>
                            <div style="
                                width: 200px;
                                height: 350px;
                                background: {bg_color};
                                border-radius: 24px;
                                padding: 40px 20px;
                                box-shadow: 0 10px 40px rgba(0,0,0,0.2);
                                display: flex;
                                flex-direction: column;
                                align-items: center;
                                justify-content: center;
                            ">
                                <img src="{icon_src}" style="width: 80px; height: 80px; border-radius: 18px; margin-bottom: 16px; box-shadow: 0 4px 12px rgba(0,0,0,0.2);" alt="App Icon"/>
                                <div style="color: white; font-size: 16px; font-weight: 600; text-shadow: 0 1px 2px rgba(0,0,0,0.2);">{app_name}</div>
                                <div style="margin-top: 24px; width: 100%;">
                                    <div style="height: 4px; background: rgba(255,255,255,0.3); border-radius: 2px; overflow: hidden;">
                                        <div style="width: 60%; height: 100%; background: white; border-radius: 2px;"></div>
                                    </div>
                                    <div style="color: rgba(255,255,255,0.8); font-size: 10px; margin-top: 8px;">Loading...</div>
                                </div>
                            </div>
                        </div>
                        
                        <!-- Color Palette -->
                        <div style="flex: 1; min-width: 200px;">
                            <div style="font-size: 12px; color: #666; margin-bottom: 8px;">🎨 Color Palette</div>
                            <div style="display: grid; gap: 8px;">
                                <div style="display: flex; align-items: center; gap: 12px;">
                                    <div style="width: 40px; height: 40px; background: {theme_color}; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);"></div>
                                    <div>
                                        <div style="font-size: 12px; font-weight: 500;">Theme Color</div>
                                        <div style="font-size: 11px; color: #888;">{theme_color}</div>
                                    </div>
                                </div>
                                <div style="display: flex; align-items: center; gap: 12px;">
                                    <div style="width: 40px; height: 40px; background: {bg_color}; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);"></div>
                                    <div>
                                        <div style="font-size: 12px; font-weight: 500;">Background</div>
                                        <div style="font-size: 11px; color: #888;">{bg_color}</div>
                                    </div>
                                </div>
                            </div>
                            
                            <div style="margin-top: 16px; padding: 12px; background: white; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.05);">
                                <div style="font-size: 11px; color: #666; margin-bottom: 4px;">Install Prompt</div>
                                <div style="display: flex; align-items: center; gap: 8px;">
                                    <img src="{icon_src}" style="width: 32px; height: 32px; border-radius: 6px;" alt="Icon"/>
                                    <div style="flex: 1;">
                                        <div style="font-size: 12px; font-weight: 500;">{app_name}</div>
                                        <div style="font-size: 10px; color: #888;">Install app?</div>
                                    </div>
                                    <button style="background: {theme_color}; color: white; border: none; padding: 6px 12px; border-radius: 6px; font-size: 11px; cursor: pointer;">Install</button>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            '''

    def action_generate_icons(self):
        """Generate all icon sizes from 512x512 icon"""
        self.ensure_one()
        if self.pwa_config_id:
            return self.pwa_config_id.action_generate_icons()

    def action_reset_pwa_defaults(self):
        """Reset PWA settings to defaults"""
        self.ensure_one()
        if self.pwa_config_id:
            self.pwa_config_id.action_reset_defaults()
        
        # Reset config parameters
        IrConfigParam = self.env['ir.config_parameter'].sudo()
        defaults = {
            'pwa.app_name': 'Odoo',
            'pwa.app_short_name': 'Odoo',
            'pwa.theme_color': '#714B67',
            'pwa.background_color': '#714B67',
            'pwa.display_mode': 'standalone',
            'pwa.orientation': 'any',
            'pwa.start_url': '/odoo',
            'pwa.scope': '/odoo',
        }
        for key, value in defaults.items():
            IrConfigParam.set_param(key, value)
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Reset Complete',
                'message': 'PWA settings have been reset to Odoo defaults.',
                'type': 'info',
                'sticky': False,
            }
        }

    def action_open_ai_branding_wizard(self):
        """Open AI branding generation wizard"""
        return {
            'type': 'ir.actions.act_window',
            'name': 'AI PWA Branding Generator',
            'res_model': 'pwa.ai.branding.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_pwa_config_id': self.pwa_config_id.id,
                'default_brand_description': self.pwa_ai_brand_prompt,
            }
        }

    def action_preview_pwa(self):
        """Open PWA preview in new tab"""
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/manifest.webmanifest',
            'target': 'new',
        }

    @api.model
    def get_values(self):
        res = super().get_values()
        IrConfigParam = self.env['ir.config_parameter'].sudo()
        
        res.update({
            'pwa_app_name': IrConfigParam.get_param('pwa.app_name', 'Odoo'),
            'pwa_app_short_name': IrConfigParam.get_param('pwa.app_short_name', 'Odoo'),
            'pwa_theme_color': IrConfigParam.get_param('pwa.theme_color', '#714B67'),
            'pwa_background_color': IrConfigParam.get_param('pwa.background_color', '#714B67'),
            'pwa_display_mode': IrConfigParam.get_param('pwa.display_mode', 'standalone'),
            'pwa_orientation': IrConfigParam.get_param('pwa.orientation', 'any'),
            'pwa_start_url': IrConfigParam.get_param('pwa.start_url', '/odoo'),
            'pwa_scope': IrConfigParam.get_param('pwa.scope', '/odoo'),
            'pwa_enable_offline': IrConfigParam.get_param('pwa.enable_offline', 'True') == 'True',
            'pwa_enable_portal': IrConfigParam.get_param('pwa.enable_portal', 'False') == 'True',
        })
        return res

    def set_values(self):
        super().set_values()
        
        # Sync settings to PWA Config model if exists
        if self.pwa_config_id:
            self.pwa_config_id.sudo().write({
                'app_name': self.pwa_app_name,
                'app_short_name': self.pwa_app_short_name,
                'app_description': self.pwa_app_description,
                'theme_color': self.pwa_theme_color,
                'background_color': self.pwa_background_color,
                'primary_color': self.pwa_primary_color,
                'secondary_color': self.pwa_secondary_color,
                'accent_color': self.pwa_accent_color,
                'display_mode': self.pwa_display_mode,
                'orientation': self.pwa_orientation,
                'start_url': self.pwa_start_url,
                'scope': self.pwa_scope,
                'splash_background_color': self.pwa_splash_background_color,
                'splash_tagline': self.pwa_splash_tagline,
                'show_splash_logo': self.pwa_show_splash_logo,
                'enable_offline': self.pwa_enable_offline,
                'offline_page_title': self.pwa_offline_page_title,
                'offline_page_message': self.pwa_offline_page_message,
                'enable_portal_pwa': self.pwa_enable_portal,
                'portal_app_name': self.pwa_portal_app_name,
                'portal_theme_color': self.pwa_portal_theme_color,
                'portal_start_url': self.pwa_portal_start_url,
                'ai_brand_prompt': self.pwa_ai_brand_prompt,
            })

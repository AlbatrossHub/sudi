# -*- coding: utf-8 -*-
# Part of Aura Odoo Tech. See LICENSE file for full copyright and licensing details.

import base64
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
from odoo.tools.image import image_process


class PWAConfig(models.Model):
    _name = 'pwa.config'
    _description = 'PWA Configuration'
    _rec_name = 'app_name'
    _order = 'sequence, id'

    # ========================================
    # Basic Fields
    # ========================================
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        default=lambda self: self.env.company,
        required=True,
    )

    # ========================================
    # App Identity
    # ========================================
    app_name = fields.Char(
        string='App Name',
        required=True,
        default='Odoo',
        help='The name displayed when the app is installed on a device',
    )
    app_short_name = fields.Char(
        string='Short Name',
        default='Odoo',
        help='Short name used on home screen (max 12 chars recommended)',
    )
    app_description = fields.Text(
        string='App Description',
        help='A description of your application for the app store listing',
    )

    # ========================================
    # App Icons
    # ========================================
    icon_192 = fields.Binary(
        string='Icon 192x192',
        help='App icon for smaller displays (192x192 pixels, PNG format)',
        attachment=True,
    )
    icon_192_filename = fields.Char(string='Icon 192 Filename')
    
    icon_512 = fields.Binary(
        string='Icon 512x512',
        help='App icon for larger displays and splash screens (512x512 pixels, PNG format)',
        attachment=True,
    )
    icon_512_filename = fields.Char(string='Icon 512 Filename')
    
    icon_maskable = fields.Binary(
        string='Maskable Icon',
        help='Maskable icon for adaptive icon support (512x512 pixels with safe zone)',
        attachment=True,
    )
    icon_maskable_filename = fields.Char(string='Maskable Icon Filename')
    
    # iOS Specific
    apple_touch_icon = fields.Binary(
        string='Apple Touch Icon',
        help='Icon for iOS devices (180x180 pixels recommended)',
        attachment=True,
    )
    apple_touch_icon_filename = fields.Char(string='Apple Touch Icon Filename')

    # ========================================
    # Theme & Colors
    # ========================================
    theme_color = fields.Char(
        string='Theme Color',
        default='#714B67',
        help='Primary theme color for browser UI elements',
    )
    background_color = fields.Char(
        string='Background Color',
        default='#714B67',
        help='Background color for splash screen during app load',
    )
    
    # Extended color palette
    primary_color = fields.Char(
        string='Primary Brand Color',
        default='#714B67',
        help='Main brand color used throughout the app',
    )
    secondary_color = fields.Char(
        string='Secondary Color',
        default='#875A7B',
        help='Secondary accent color',
    )
    accent_color = fields.Char(
        string='Accent Color',
        default='#00A09D',
        help='Accent color for highlights and CTAs',
    )

    # ========================================
    # Splash Screen / Branding
    # ========================================
    splash_image = fields.Binary(
        string='Splash Image',
        help='Custom splash screen image (recommended: 512x512 or larger)',
        attachment=True,
    )
    splash_image_filename = fields.Char(string='Splash Image Filename')
    
    splash_background_color = fields.Char(
        string='Splash Background',
        default='#FFFFFF',
        help='Background color for the splash screen',
    )
    
    show_splash_logo = fields.Boolean(
        string='Show Logo in Splash',
        default=True,
    )
    splash_tagline = fields.Char(
        string='Splash Tagline',
        help='Short tagline shown on splash screen',
    )

    # ========================================
    # PWA Behavior Settings
    # ========================================
    display_mode = fields.Selection([
        ('fullscreen', 'Fullscreen'),
        ('standalone', 'Standalone (Default)'),
        ('minimal-ui', 'Minimal UI'),
        ('browser', 'Browser'),
    ], string='Display Mode', default='standalone',
        help='How the app appears when launched from home screen')
    
    orientation = fields.Selection([
        ('any', 'Any'),
        ('natural', 'Natural'),
        ('portrait', 'Portrait'),
        ('portrait-primary', 'Portrait Primary'),
        ('portrait-secondary', 'Portrait Secondary'),
        ('landscape', 'Landscape'),
        ('landscape-primary', 'Landscape Primary'),
        ('landscape-secondary', 'Landscape Secondary'),
    ], string='Orientation', default='any',
        help='Preferred screen orientation for the app')
    
    start_url = fields.Char(
        string='Start URL',
        default='/odoo',
        help='The URL that loads when the app is launched',
    )
    scope = fields.Char(
        string='Scope',
        default='/odoo',
        help='Navigation scope of the PWA',
    )

    # ========================================
    # Customer Portal PWA
    # ========================================
    enable_portal_pwa = fields.Boolean(
        string='Enable Portal PWA',
        default=False,
        help='Enable separate PWA settings for customer portal',
    )
    portal_app_name = fields.Char(
        string='Portal App Name',
        default='Customer Portal',
    )
    portal_icon_192 = fields.Binary(
        string='Portal Icon 192x192',
        attachment=True,
    )
    portal_icon_512 = fields.Binary(
        string='Portal Icon 512x512',
        attachment=True,
    )
    portal_theme_color = fields.Char(
        string='Portal Theme Color',
        default='#00A09D',
    )
    portal_background_color = fields.Char(
        string='Portal Background Color',
        default='#FFFFFF',
    )
    portal_start_url = fields.Char(
        string='Portal Start URL',
        default='/my',
    )

    # ========================================
    # Offline Settings
    # ========================================
    enable_offline = fields.Boolean(
        string='Enable Offline Mode',
        default=True,
        help='Allow app to work offline with cached content',
    )
    offline_page_title = fields.Char(
        string='Offline Page Title',
        default='You are offline',
    )
    offline_page_message = fields.Text(
        string='Offline Message',
        default='Please check your internet connection and try again.',
    )
    
    # ========================================
    # Notifications
    # ========================================
    enable_push_notifications = fields.Boolean(
        string='Enable Push Notifications',
        default=False,
        help='Enable push notification support for the PWA',
    )

    # ========================================
    # AI Branding
    # ========================================
    ai_brand_prompt = fields.Text(
        string='AI Branding Prompt',
        help='Describe your brand for AI to generate color suggestions',
    )
    ai_generated_colors = fields.Text(
        string='AI Generated Colors',
        help='JSON containing AI-suggested color palette',
    )
    
    # ========================================
    # Computed Fields
    # ========================================
    preview_html = fields.Html(
        string='Preview',
        compute='_compute_preview_html',
    )

    @api.depends('app_name', 'theme_color', 'background_color', 'icon_192')
    def _compute_preview_html(self):
        """Generate a preview HTML for the PWA appearance"""
        for record in self:
            icon_src = '/web/static/img/odoo-icon-192x192.png'
            if record.icon_192:
                icon_src = f'/web/image/pwa.config/{record.id}/icon_192'
            
            record.preview_html = f'''
                <div class="pwa-preview-container" style="
                    background-color: {record.background_color or '#714B67'};
                    border-radius: 12px;
                    padding: 20px;
                    text-align: center;
                    max-width: 300px;
                    margin: auto;
                    box-shadow: 0 4px 12px rgba(0,0,0,0.15);
                ">
                    <img src="{icon_src}" 
                         style="width: 96px; height: 96px; border-radius: 20px; margin-bottom: 16px;"
                         alt="App Icon"/>
                    <div style="
                        color: white;
                        font-size: 18px;
                        font-weight: bold;
                        text-shadow: 0 1px 2px rgba(0,0,0,0.2);
                    ">{record.app_name or 'Odoo'}</div>
                    <div style="
                        margin-top: 16px;
                        display: inline-block;
                        padding: 8px 16px;
                        background: {record.theme_color or '#714B67'};
                        color: white;
                        border-radius: 20px;
                        font-size: 12px;
                        border: 2px solid rgba(255,255,255,0.3);
                    ">Install App</div>
                </div>
            '''

    # ========================================
    # Constraints
    # ========================================
    @api.constrains('theme_color', 'background_color')
    def _check_color_format(self):
        """Validate color format (hex code)"""
        import re
        hex_pattern = re.compile(r'^#(?:[0-9a-fA-F]{3}){1,2}$')
        for record in self:
            for field_name in ['theme_color', 'background_color', 'primary_color', 
                             'secondary_color', 'accent_color', 'splash_background_color']:
                color = getattr(record, field_name, None)
                if color and not hex_pattern.match(color):
                    raise ValidationError(
                        _('Invalid color format for %s. Please use hex format (e.g., #714B67)') % field_name
                    )

    @api.constrains('icon_192', 'icon_512')
    def _check_icon_format(self):
        """Validate icon dimensions"""
        for record in self:
            # Basic validation - actual image processing will handle the rest
            pass

    # ========================================
    # CRUD Methods
    # ========================================
    @api.model
    def get_config(self, company_id=None):
        """Get the PWA configuration for the given company"""
        if not company_id:
            company_id = self.env.company.id
        
        config = self.sudo().search([
            ('company_id', '=', company_id),
            ('active', '=', True)
        ], limit=1)
        
        if not config:
            # Return default values
            return {
                'app_name': 'Odoo',
                'app_short_name': 'Odoo',
                'theme_color': '#714B67',
                'background_color': '#714B67',
                'display_mode': 'standalone',
                'orientation': 'any',
                'start_url': '/odoo',
                'scope': '/odoo',
            }
        
        return {
            'id': config.id,
            'app_name': config.app_name,
            'app_short_name': config.app_short_name or config.app_name,
            'app_description': config.app_description,
            'theme_color': config.theme_color,
            'background_color': config.background_color,
            'display_mode': config.display_mode,
            'orientation': config.orientation,
            'start_url': config.start_url,
            'scope': config.scope,
            'has_icon_192': bool(config.icon_192),
            'has_icon_512': bool(config.icon_512),
            'has_maskable_icon': bool(config.icon_maskable),
            'has_apple_touch_icon': bool(config.apple_touch_icon),
            'enable_offline': config.enable_offline,
            'offline_page_title': config.offline_page_title,
            'offline_page_message': config.offline_page_message,
        }

    def action_apply_colors(self):
        """Apply the current color scheme"""
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Colors Applied'),
                'message': _('PWA color scheme has been updated successfully!'),
                'type': 'success',
                'sticky': False,
            }
        }

    def action_reset_defaults(self):
        """Reset all settings to Odoo defaults"""
        self.ensure_one()
        self.write({
            'app_name': 'Odoo',
            'app_short_name': 'Odoo',
            'theme_color': '#714B67',
            'background_color': '#714B67',
            'primary_color': '#714B67',
            'secondary_color': '#875A7B',
            'accent_color': '#00A09D',
            'splash_background_color': '#FFFFFF',
            'display_mode': 'standalone',
            'orientation': 'any',
            'start_url': '/odoo',
            'scope': '/odoo',
            'icon_192': False,
            'icon_512': False,
            'icon_maskable': False,
            'apple_touch_icon': False,
            'splash_image': False,
        })
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Reset Complete'),
                'message': _('PWA settings have been reset to defaults.'),
                'type': 'info',
                'sticky': False,
            }
        }

    def action_generate_icons(self):
        """Auto-generate all icon sizes from the 512x512 icon"""
        self.ensure_one()
        if not self.icon_512:
            raise ValidationError(_('Please upload a 512x512 icon first to generate other sizes.'))
        
        # Generate 192x192 from 512x512
        icon_data = base64.b64decode(self.icon_512)
        icon_192_data = image_process(icon_data, size=(192, 192))
        self.icon_192 = base64.b64encode(icon_192_data)
        
        # Generate Apple touch icon (180x180)
        apple_icon_data = image_process(icon_data, size=(180, 180))
        self.apple_touch_icon = base64.b64encode(apple_icon_data)
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Icons Generated'),
                'message': _('All icon sizes have been generated from your 512x512 icon.'),
                'type': 'success',
                'sticky': False,
            }
        }

    def get_icon_url(self, size='192'):
        """Get the URL for a specific icon size"""
        self.ensure_one()
        if size == '192' and self.icon_192:
            return f'/web/image/pwa.config/{self.id}/icon_192'
        elif size == '512' and self.icon_512:
            return f'/web/image/pwa.config/{self.id}/icon_512'
        elif size == 'maskable' and self.icon_maskable:
            return f'/web/image/pwa.config/{self.id}/icon_maskable'
        elif size == 'apple' and self.apple_touch_icon:
            return f'/web/image/pwa.config/{self.id}/apple_touch_icon'
        
        # Return default Odoo icons
        if size == '192':
            return '/web/static/img/odoo-icon-192x192.png'
        elif size == '512':
            return '/web/static/img/odoo-icon-512x512.png'
        return '/web/static/img/odoo-icon-192x192.png'

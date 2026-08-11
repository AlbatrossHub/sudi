# -*- coding: utf-8 -*-
# Part of Aura Odoo Tech. See LICENSE file for full copyright and licensing details.

import base64
import mimetypes

from odoo import http
from odoo.http import request
from odoo.tools import file_open
from odoo.tools.image import image_process
from odoo.addons.web.controllers.webmanifest import WebManifest


class DynamicWebManifest(WebManifest):
    """
    Override the default WebManifest controller to provide dynamic PWA configuration.
    All PWA settings (name, colors, icons, etc.) are now configurable from Settings.
    """

    def _get_pwa_config(self):
        """Get PWA configuration from database or return defaults"""
        IrConfigParam = request.env['ir.config_parameter'].sudo()
        PWAConfig = request.env['pwa.config'].sudo()
        
        # Try to get company-specific config
        company_id = request.env.company.id
        config = PWAConfig.search([
            ('company_id', '=', company_id),
            ('active', '=', True)
        ], limit=1)
        
        # Build config dict from system parameters with fallbacks
        return {
            'app_name': IrConfigParam.get_param('pwa.app_name') or 
                       (config.app_name if config else False) or 
                       IrConfigParam.get_param('web.web_app_name', 'Odoo'),
            'app_short_name': IrConfigParam.get_param('pwa.app_short_name') or 
                             (config.app_short_name if config else False) or 'Odoo',
            'app_description': IrConfigParam.get_param('pwa.app_description') or 
                              (config.app_description if config else False) or '',
            'theme_color': IrConfigParam.get_param('pwa.theme_color') or 
                          (config.theme_color if config else False) or '#714B67',
            'background_color': IrConfigParam.get_param('pwa.background_color') or 
                               (config.background_color if config else False) or '#714B67',
            'display_mode': IrConfigParam.get_param('pwa.display_mode') or 
                           (config.display_mode if config else False) or 'standalone',
            'orientation': IrConfigParam.get_param('pwa.orientation') or 
                          (config.orientation if config else False) or 'any',
            'start_url': IrConfigParam.get_param('pwa.start_url') or 
                        (config.start_url if config else False) or '/odoo',
            'scope': IrConfigParam.get_param('pwa.scope') or 
                    (config.scope if config else False) or '/odoo',
            'config_id': config.id if config else False,
            'has_icon_192': config.icon_192 if config else False,
            'has_icon_512': config.icon_512 if config else False,
            'has_maskable_icon': config.icon_maskable if config else False,
        }

    def _get_webmanifest(self):
        """
        Override to use dynamic PWA configuration.
        Returns a WebManifest with user-configured settings.
        """
        config = self._get_pwa_config()
        
        manifest = {
            'name': config['app_name'],
            'short_name': config['app_short_name'],
            'description': config['app_description'] or f"{config['app_name']} - Progressive Web App",
            'scope': config['scope'],
            'start_url': config['start_url'],
            'display': config['display_mode'],
            'orientation': config['orientation'],
            'background_color': config['background_color'],
            'theme_color': config['theme_color'],
            'prefer_related_applications': False,
            'categories': ['business', 'productivity'],
        }
        
        # Build icons list
        icons = []
        
        # Add custom icons if available
        if config['config_id']:
            if config['has_icon_192']:
                icons.append({
                    'src': f'/pwa/icon/192?config_id={config["config_id"]}',
                    'sizes': '192x192',
                    'type': 'image/png',
                    'purpose': 'any',
                })
            if config['has_icon_512']:
                icons.append({
                    'src': f'/pwa/icon/512?config_id={config["config_id"]}',
                    'sizes': '512x512',
                    'type': 'image/png',
                    'purpose': 'any',
                })
            if config['has_maskable_icon']:
                icons.append({
                    'src': f'/pwa/icon/maskable?config_id={config["config_id"]}',
                    'sizes': '512x512',
                    'type': 'image/png',
                    'purpose': 'maskable',
                })
        
        # Fall back to default Odoo icons if no custom icons
        if not icons:
            icon_sizes = ['192x192', '512x512']
            icons = [{
                'src': f'/web/static/img/odoo-icon-{size}.png',
                'sizes': size,
                'type': 'image/png',
            } for size in icon_sizes]
        
        manifest['icons'] = icons
        manifest['shortcuts'] = self._get_shortcuts()
        
        return manifest

    @http.route('/pwa/icon/<string:size>', type='http', auth='public', methods=['GET'])
    def pwa_icon(self, size, config_id=None, **kwargs):
        """
        Serve custom PWA icons based on configuration.
        Supports sizes: 192, 512, maskable, apple
        """
        PWAConfig = request.env['pwa.config'].sudo()
        
        if config_id:
            config = PWAConfig.browse(int(config_id))
        else:
            config = PWAConfig.search([
                ('company_id', '=', request.env.company.id),
                ('active', '=', True)
            ], limit=1)
        
        icon_data = None
        content_type = 'image/png'
        
        if config:
            if size == '192' and config.icon_192:
                icon_data = base64.b64decode(config.icon_192)
            elif size == '512' and config.icon_512:
                icon_data = base64.b64decode(config.icon_512)
            elif size == 'maskable' and config.icon_maskable:
                icon_data = base64.b64decode(config.icon_maskable)
            elif size == 'apple' and config.apple_touch_icon:
                icon_data = base64.b64decode(config.apple_touch_icon)
        
        if not icon_data:
            # Serve default Odoo icon
            icon_path = f'web/static/img/odoo-icon-{size}x{size}.png'
            if size in ['maskable', 'apple']:
                icon_path = 'web/static/img/odoo-icon-192x192.png'
            
            try:
                with file_open(icon_path, 'rb') as f:
                    icon_data = f.read()
            except FileNotFoundError:
                with file_open('web/static/img/odoo-icon-192x192.png', 'rb') as f:
                    icon_data = f.read()
        
        return request.make_response(icon_data, headers=[
            ('Content-Type', content_type),
            ('Cache-Control', 'public, max-age=604800'),  # Cache for 7 days
        ])

    @http.route('/pwa/apple-touch-icon.png', type='http', auth='public', methods=['GET'])
    def apple_touch_icon(self, **kwargs):
        """Serve Apple touch icon for iOS devices"""
        return self.pwa_icon('apple', **kwargs)

    @http.route('/pwa/splash', type='http', auth='public', methods=['GET'])
    def pwa_splash(self, **kwargs):
        """Serve custom splash screen image"""
        PWAConfig = request.env['pwa.config'].sudo()
        config = PWAConfig.search([
            ('company_id', '=', request.env.company.id),
            ('active', '=', True)
        ], limit=1)
        
        if config and config.splash_image:
            image_data = base64.b64decode(config.splash_image)
            return request.make_response(image_data, headers=[
                ('Content-Type', 'image/png'),
                ('Cache-Control', 'public, max-age=604800'),
            ])
        
        # Return default icon as splash fallback
        return self.pwa_icon('512')

    def _get_service_worker_content(self):
        """
        Override to inject dynamic configuration into service worker.
        """
        config = self._get_pwa_config()
        
        with file_open('web/static/src/service_worker.js') as f:
            sw_content = f.read()
        
        # Inject dynamic config as comments/constants at the top
        config_injection = f'''
// Dynamic PWA Configuration - Generated by pwa_dynamic_branding module
const PWA_CONFIG = {{
    appName: "{config['app_name']}",
    themeColor: "{config['theme_color']}",
    backgroundColor: "{config['background_color']}",
    startUrl: "{config['start_url']}",
    scope: "{config['scope']}",
}};
// End Dynamic PWA Configuration

'''
        return config_injection + sw_content

    @http.route('/odoo/offline', type='http', auth='public', methods=['GET'], readonly=True)
    def offline(self):
        """
        Override offline page to use dynamic branding.
        """
        config = self._get_pwa_config()
        PWAConfig = request.env['pwa.config'].sudo()
        pwa_config = PWAConfig.browse(config['config_id']) if config['config_id'] else None
        
        # Get icon for offline page
        if pwa_config and pwa_config.icon_192:
            odoo_icon = pwa_config.icon_192
        else:
            odoo_icon = base64.b64encode(file_open(self._icon_path(), 'rb').read())
        
        # Get offline message from config
        offline_title = 'You are offline'
        offline_message = 'Please check your internet connection and try again.'
        
        if pwa_config:
            offline_title = pwa_config.offline_page_title or offline_title
            offline_message = pwa_config.offline_page_message or offline_message
        
        return request.render('web.webclient_offline', {
            'odoo_icon': odoo_icon,
            'app_name': config['app_name'],
            'theme_color': config['theme_color'],
            'background_color': config['background_color'],
            'offline_title': offline_title,
            'offline_message': offline_message,
        })

    # ========================================
    # Portal PWA Support
    # ========================================
    @http.route('/my/manifest.webmanifest', type='http', auth='public', methods=['GET'], readonly=True)
    def portal_webmanifest(self):
        """
        Separate WebManifest for customer portal PWA.
        Allows different branding for customer-facing portal.
        """
        IrConfigParam = request.env['ir.config_parameter'].sudo()
        
        # Check if portal PWA is enabled
        if IrConfigParam.get_param('pwa.enable_portal', 'False') != 'True':
            # Return standard manifest
            return self.webmanifest()
        
        PWAConfig = request.env['pwa.config'].sudo()
        config = PWAConfig.search([
            ('company_id', '=', request.env.company.id),
            ('active', '=', True),
            ('enable_portal_pwa', '=', True)
        ], limit=1)
        
        portal_name = IrConfigParam.get_param('pwa.portal_app_name', 'Customer Portal')
        portal_theme = IrConfigParam.get_param('pwa.portal_theme_color', '#00A09D')
        portal_start = IrConfigParam.get_param('pwa.portal_start_url', '/my')
        
        manifest = {
            'name': config.portal_app_name if config else portal_name,
            'short_name': 'Portal',
            'scope': '/my',
            'start_url': config.portal_start_url if config else portal_start,
            'display': 'standalone',
            'background_color': config.portal_background_color if config else '#FFFFFF',
            'theme_color': config.portal_theme_color if config else portal_theme,
            'prefer_related_applications': False,
        }
        
        # Portal icons
        icons = []
        if config and config.portal_icon_192:
            icons.append({
                'src': f'/pwa/portal/icon/192?config_id={config.id}',
                'sizes': '192x192',
                'type': 'image/png',
            })
        if config and config.portal_icon_512:
            icons.append({
                'src': f'/pwa/portal/icon/512?config_id={config.id}',
                'sizes': '512x512',
                'type': 'image/png',
            })
        
        if not icons:
            icons = [{
                'src': '/web/static/img/odoo-icon-192x192.png',
                'sizes': '192x192',
                'type': 'image/png',
            }, {
                'src': '/web/static/img/odoo-icon-512x512.png',
                'sizes': '512x512',
                'type': 'image/png',
            }]
        
        manifest['icons'] = icons
        
        return request.make_json_response(manifest, {
            'Content-Type': 'application/manifest+json'
        })

    @http.route('/pwa/portal/icon/<string:size>', type='http', auth='public', methods=['GET'])
    def portal_pwa_icon(self, size, config_id=None, **kwargs):
        """Serve portal-specific PWA icons"""
        PWAConfig = request.env['pwa.config'].sudo()
        
        if config_id:
            config = PWAConfig.browse(int(config_id))
        else:
            config = PWAConfig.search([
                ('company_id', '=', request.env.company.id),
                ('active', '=', True)
            ], limit=1)
        
        icon_data = None
        
        if config:
            if size == '192' and config.portal_icon_192:
                icon_data = base64.b64decode(config.portal_icon_192)
            elif size == '512' and config.portal_icon_512:
                icon_data = base64.b64decode(config.portal_icon_512)
        
        if not icon_data:
            return self.pwa_icon(size, config_id, **kwargs)
        
        return request.make_response(icon_data, headers=[
            ('Content-Type', 'image/png'),
            ('Cache-Control', 'public, max-age=604800'),
        ])

    # ========================================
    # PWA Info API
    # ========================================
    @http.route('/pwa/config', type='json', auth='public', methods=['POST'])
    def get_pwa_config_json(self, **kwargs):
        """
        JSON API to get current PWA configuration.
        Useful for JavaScript components that need PWA info.
        """
        config = self._get_pwa_config()
        return {
            'success': True,
            'config': {
                'name': config['app_name'],
                'shortName': config['app_short_name'],
                'themeColor': config['theme_color'],
                'backgroundColor': config['background_color'],
                'startUrl': config['start_url'],
                'scope': config['scope'],
                'displayMode': config['display_mode'],
                'orientation': config['orientation'],
                'hasCustomIcons': bool(config['has_icon_192'] or config['has_icon_512']),
            }
        }

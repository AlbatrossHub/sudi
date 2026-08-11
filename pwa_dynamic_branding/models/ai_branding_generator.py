# -*- coding: utf-8 -*-
# Part of Aura Odoo Tech. See LICENSE file for full copyright and licensing details.

import json
import logging
from odoo import api, fields, models, _

_logger = logging.getLogger(__name__)


class PWAAIBrandingGenerator(models.Model):
    _name = 'pwa.ai.branding'
    _description = 'PWA AI Branding Generator'
    _order = 'create_date desc'

    name = fields.Char(string='Name', required=True)
    pwa_config_id = fields.Many2one('pwa.config', string='PWA Configuration')
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)
    
    # Brand Input
    brand_description = fields.Text(
        string='Brand Description',
        help='Describe your brand, industry, values, and target audience',
    )
    industry = fields.Selection([
        ('technology', 'Technology'),
        ('healthcare', 'Healthcare'),
        ('finance', 'Finance & Banking'),
        ('retail', 'Retail & E-commerce'),
        ('education', 'Education'),
        ('food', 'Food & Restaurant'),
        ('travel', 'Travel & Hospitality'),
        ('manufacturing', 'Manufacturing'),
        ('real_estate', 'Real Estate'),
        ('entertainment', 'Entertainment'),
        ('nonprofit', 'Non-Profit'),
        ('other', 'Other'),
    ], string='Industry')
    
    brand_personality = fields.Selection([
        ('professional', 'Professional & Corporate'),
        ('friendly', 'Friendly & Approachable'),
        ('luxurious', 'Luxurious & Premium'),
        ('playful', 'Playful & Fun'),
        ('minimalist', 'Minimalist & Clean'),
        ('bold', 'Bold & Dynamic'),
        ('natural', 'Natural & Organic'),
        ('tech', 'Tech & Modern'),
    ], string='Brand Personality')
    
    target_audience = fields.Char(string='Target Audience')
    existing_brand_colors = fields.Char(string='Existing Brand Colors (if any)')
    
    # AI Generated Output
    generated_palette = fields.Text(string='Generated Color Palette (JSON)')
    generated_theme_color = fields.Char(string='Generated Theme Color')
    generated_background_color = fields.Char(string='Generated Background Color')
    generated_primary_color = fields.Char(string='Generated Primary Color')
    generated_secondary_color = fields.Char(string='Generated Secondary Color')
    generated_accent_color = fields.Char(string='Generated Accent Color')
    generated_app_name_suggestions = fields.Text(string='App Name Suggestions')
    generated_tagline = fields.Char(string='Generated Tagline')
    
    # Status
    state = fields.Selection([
        ('draft', 'Draft'),
        ('generated', 'Generated'),
        ('applied', 'Applied'),
    ], default='draft', string='Status')

    @api.model
    def get_ai_prompt_template(self, brand_info):
        """Generate AI prompt for brand colors"""
        return f"""
You are a professional brand designer. Generate a cohesive PWA (Progressive Web App) color palette based on the following brand information:

Brand Description: {brand_info.get('description', 'A modern business application')}
Industry: {brand_info.get('industry', 'Technology')}
Brand Personality: {brand_info.get('personality', 'Professional')}
Target Audience: {brand_info.get('target_audience', 'Business professionals')}
Existing Colors: {brand_info.get('existing_colors', 'None specified')}

Please provide:
1. Theme Color (hex) - Primary browser UI color
2. Background Color (hex) - Splash screen background
3. Primary Color (hex) - Main brand color
4. Secondary Color (hex) - Supporting color
5. Accent Color (hex) - Highlight/CTA color

Also suggest:
- 3 App name variations
- A short tagline (max 50 characters)

Format your response as JSON:
{{
    "theme_color": "#XXXXXX",
    "background_color": "#XXXXXX", 
    "primary_color": "#XXXXXX",
    "secondary_color": "#XXXXXX",
    "accent_color": "#XXXXXX",
    "app_names": ["Name1", "Name2", "Name3"],
    "tagline": "Your tagline here",
    "reasoning": "Brief explanation of color choices"
}}
"""

    def action_generate_ai_branding(self):
        """Generate AI branding suggestions"""
        self.ensure_one()
        
        # Prepare brand info
        brand_info = {
            'description': self.brand_description,
            'industry': dict(self._fields['industry'].selection).get(self.industry, ''),
            'personality': dict(self._fields['brand_personality'].selection).get(self.brand_personality, ''),
            'target_audience': self.target_audience,
            'existing_colors': self.existing_brand_colors,
        }
        
        # Get AI prompt
        prompt = self.get_ai_prompt_template(brand_info)
        
        # For now, generate intelligent defaults based on industry/personality
        # This can be extended to use actual AI API
        colors = self._generate_smart_colors()
        
        self.write({
            'generated_theme_color': colors['theme_color'],
            'generated_background_color': colors['background_color'],
            'generated_primary_color': colors['primary_color'],
            'generated_secondary_color': colors['secondary_color'],
            'generated_accent_color': colors['accent_color'],
            'generated_palette': json.dumps(colors, indent=2),
            'generated_app_name_suggestions': '\n'.join(colors.get('app_names', [])),
            'generated_tagline': colors.get('tagline', ''),
            'state': 'generated',
        })
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Branding Generated'),
                'message': _('AI has generated your PWA branding suggestions!'),
                'type': 'success',
                'sticky': False,
            }
        }

    def _generate_smart_colors(self):
        """Generate intelligent color palette based on industry and personality"""
        # Color palettes by industry
        industry_palettes = {
            'technology': {
                'theme_color': '#2196F3',
                'background_color': '#1976D2',
                'primary_color': '#2196F3',
                'secondary_color': '#64B5F6',
                'accent_color': '#00BCD4',
            },
            'healthcare': {
                'theme_color': '#00897B',
                'background_color': '#004D40',
                'primary_color': '#00897B',
                'secondary_color': '#4DB6AC',
                'accent_color': '#26A69A',
            },
            'finance': {
                'theme_color': '#1565C0',
                'background_color': '#0D47A1',
                'primary_color': '#1565C0',
                'secondary_color': '#42A5F5',
                'accent_color': '#FFB300',
            },
            'retail': {
                'theme_color': '#E91E63',
                'background_color': '#C2185B',
                'primary_color': '#E91E63',
                'secondary_color': '#F48FB1',
                'accent_color': '#FF5722',
            },
            'education': {
                'theme_color': '#673AB7',
                'background_color': '#512DA8',
                'primary_color': '#673AB7',
                'secondary_color': '#9575CD',
                'accent_color': '#FF9800',
            },
            'food': {
                'theme_color': '#FF5722',
                'background_color': '#E64A19',
                'primary_color': '#FF5722',
                'secondary_color': '#FFAB91',
                'accent_color': '#4CAF50',
            },
            'travel': {
                'theme_color': '#00ACC1',
                'background_color': '#00838F',
                'primary_color': '#00ACC1',
                'secondary_color': '#4DD0E1',
                'accent_color': '#FFC107',
            },
            'manufacturing': {
                'theme_color': '#455A64',
                'background_color': '#37474F',
                'primary_color': '#455A64',
                'secondary_color': '#78909C',
                'accent_color': '#FF9800',
            },
            'real_estate': {
                'theme_color': '#795548',
                'background_color': '#5D4037',
                'primary_color': '#795548',
                'secondary_color': '#A1887F',
                'accent_color': '#4CAF50',
            },
            'entertainment': {
                'theme_color': '#9C27B0',
                'background_color': '#7B1FA2',
                'primary_color': '#9C27B0',
                'secondary_color': '#CE93D8',
                'accent_color': '#FFEB3B',
            },
            'nonprofit': {
                'theme_color': '#4CAF50',
                'background_color': '#388E3C',
                'primary_color': '#4CAF50',
                'secondary_color': '#81C784',
                'accent_color': '#2196F3',
            },
        }
        
        # Personality adjustments
        personality_adjustments = {
            'luxurious': {'accent_color': '#FFD700'},
            'playful': {'accent_color': '#FF4081'},
            'minimalist': {'secondary_color': '#BDBDBD'},
            'bold': {'accent_color': '#FF1744'},
        }
        
        # Get base palette
        palette = industry_palettes.get(self.industry, {
            'theme_color': '#714B67',
            'background_color': '#714B67',
            'primary_color': '#714B67',
            'secondary_color': '#875A7B',
            'accent_color': '#00A09D',
        })
        
        # Apply personality adjustments
        if self.brand_personality in personality_adjustments:
            palette.update(personality_adjustments[self.brand_personality])
        
        # Add app name suggestions and tagline
        industry_names = {
            'technology': ['TechHub', 'InnovatePro', 'DigitalFlow'],
            'healthcare': ['HealthCare+', 'MedConnect', 'WellnessHub'],
            'finance': ['FinanceMax', 'WealthWise', 'MoneyFlow'],
            'retail': ['ShopSmart', 'RetailPro', 'StoreHub'],
            'education': ['LearnHub', 'EduPro', 'KnowledgeBase'],
            'food': ['FoodieApp', 'TasteHub', 'DineEasy'],
            'travel': ['TravelMate', 'ExploreMore', 'JourneyPro'],
        }
        
        palette['app_names'] = industry_names.get(self.industry, ['MyApp', 'BusinessPro', 'SmartHub'])
        
        taglines = {
            'technology': 'Innovate. Connect. Succeed.',
            'healthcare': 'Your Health, Our Priority',
            'finance': 'Smart Money Management',
            'retail': 'Shop Smarter, Live Better',
            'education': 'Learn Without Limits',
            'food': 'Delicious Made Easy',
            'travel': 'Adventure Awaits',
        }
        palette['tagline'] = taglines.get(self.industry, 'Your Business, Simplified')
        
        return palette

    def action_apply_to_pwa(self):
        """Apply generated branding to PWA config"""
        self.ensure_one()
        
        if not self.pwa_config_id:
            # Find or create PWA config
            config = self.env['pwa.config'].sudo().search([
                ('company_id', '=', self.company_id.id),
                ('active', '=', True)
            ], limit=1)
            
            if not config:
                config = self.env['pwa.config'].sudo().create({
                    'company_id': self.company_id.id,
                    'app_name': self.name,
                })
            self.pwa_config_id = config.id
        
        # Apply colors to PWA config
        self.pwa_config_id.write({
            'theme_color': self.generated_theme_color,
            'background_color': self.generated_background_color,
            'primary_color': self.generated_primary_color,
            'secondary_color': self.generated_secondary_color,
            'accent_color': self.generated_accent_color,
            'splash_tagline': self.generated_tagline,
            'ai_generated_colors': self.generated_palette,
        })
        
        # Also update system parameters
        IrConfigParam = self.env['ir.config_parameter'].sudo()
        IrConfigParam.set_param('pwa.theme_color', self.generated_theme_color)
        IrConfigParam.set_param('pwa.background_color', self.generated_background_color)
        IrConfigParam.set_param('pwa.primary_color', self.generated_primary_color)
        IrConfigParam.set_param('pwa.secondary_color', self.generated_secondary_color)
        IrConfigParam.set_param('pwa.accent_color', self.generated_accent_color)
        
        self.state = 'applied'
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Branding Applied'),
                'message': _('Your AI-generated branding has been applied to the PWA!'),
                'type': 'success',
                'sticky': False,
            }
        }


class PWAAIBrandingWizard(models.TransientModel):
    _name = 'pwa.ai.branding.wizard'
    _description = 'PWA AI Branding Wizard'

    pwa_config_id = fields.Many2one('pwa.config', string='PWA Configuration')
    
    # Input
    brand_description = fields.Text(
        string='Describe Your Brand',
        help='Tell us about your business, values, and target audience',
        default="A modern business looking to enhance customer engagement through mobile apps.",
    )
    industry = fields.Selection([
        ('technology', 'Technology & Software'),
        ('healthcare', 'Healthcare & Medical'),
        ('finance', 'Finance & Banking'),
        ('retail', 'Retail & E-commerce'),
        ('education', 'Education & Training'),
        ('food', 'Food & Restaurant'),
        ('travel', 'Travel & Hospitality'),
        ('manufacturing', 'Manufacturing & Industrial'),
        ('real_estate', 'Real Estate & Property'),
        ('entertainment', 'Entertainment & Media'),
        ('nonprofit', 'Non-Profit & NGO'),
        ('other', 'Other'),
    ], string='Industry', default='technology')
    
    brand_personality = fields.Selection([
        ('professional', '👔 Professional & Corporate'),
        ('friendly', '😊 Friendly & Approachable'),
        ('luxurious', '✨ Luxurious & Premium'),
        ('playful', '🎉 Playful & Fun'),
        ('minimalist', '◻️ Minimalist & Clean'),
        ('bold', '💪 Bold & Dynamic'),
        ('natural', '🌿 Natural & Organic'),
        ('tech', '🚀 Tech & Modern'),
    ], string='Brand Personality', default='professional')
    
    # Generated Preview
    preview_theme_color = fields.Char(string='Theme Color', default='#714B67')
    preview_background_color = fields.Char(string='Background', default='#714B67')
    preview_primary_color = fields.Char(string='Primary', default='#714B67')
    preview_secondary_color = fields.Char(string='Secondary', default='#875A7B')
    preview_accent_color = fields.Char(string='Accent', default='#00A09D')
    preview_tagline = fields.Char(string='Tagline')
    preview_html = fields.Html(string='Preview', compute='_compute_preview')

    @api.depends('preview_theme_color', 'preview_background_color', 'preview_primary_color',
                 'preview_secondary_color', 'preview_accent_color')
    def _compute_preview(self):
        for record in self:
            record.preview_html = f'''
                <div style="display: flex; gap: 8px; flex-wrap: wrap; padding: 12px; background: #f8f9fa; border-radius: 8px;">
                    <div style="text-align: center;">
                        <div style="width: 50px; height: 50px; background: {record.preview_theme_color}; border-radius: 8px; margin-bottom: 4px;"></div>
                        <div style="font-size: 10px; color: #666;">Theme</div>
                    </div>
                    <div style="text-align: center;">
                        <div style="width: 50px; height: 50px; background: {record.preview_background_color}; border-radius: 8px; margin-bottom: 4px;"></div>
                        <div style="font-size: 10px; color: #666;">Background</div>
                    </div>
                    <div style="text-align: center;">
                        <div style="width: 50px; height: 50px; background: {record.preview_primary_color}; border-radius: 8px; margin-bottom: 4px;"></div>
                        <div style="font-size: 10px; color: #666;">Primary</div>
                    </div>
                    <div style="text-align: center;">
                        <div style="width: 50px; height: 50px; background: {record.preview_secondary_color}; border-radius: 8px; margin-bottom: 4px;"></div>
                        <div style="font-size: 10px; color: #666;">Secondary</div>
                    </div>
                    <div style="text-align: center;">
                        <div style="width: 50px; height: 50px; background: {record.preview_accent_color}; border-radius: 8px; margin-bottom: 4px;"></div>
                        <div style="font-size: 10px; color: #666;">Accent</div>
                    </div>
                </div>
            '''

    def action_generate_preview(self):
        """Generate color preview based on selections"""
        self.ensure_one()
        
        # Create temporary branding generator
        generator = self.env['pwa.ai.branding'].create({
            'name': f'Preview - {fields.Datetime.now()}',
            'brand_description': self.brand_description,
            'industry': self.industry,
            'brand_personality': self.brand_personality,
            'company_id': self.env.company.id,
        })
        
        colors = generator._generate_smart_colors()
        
        self.write({
            'preview_theme_color': colors['theme_color'],
            'preview_background_color': colors['background_color'],
            'preview_primary_color': colors['primary_color'],
            'preview_secondary_color': colors['secondary_color'],
            'preview_accent_color': colors['accent_color'],
            'preview_tagline': colors.get('tagline', ''),
        })
        
        # Clean up temporary record
        generator.unlink()
        
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'pwa.ai.branding.wizard',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_apply_branding(self):
        """Apply the generated branding"""
        self.ensure_one()
        
        # Update PWA config
        if self.pwa_config_id:
            self.pwa_config_id.write({
                'theme_color': self.preview_theme_color,
                'background_color': self.preview_background_color,
                'primary_color': self.preview_primary_color,
                'secondary_color': self.preview_secondary_color,
                'accent_color': self.preview_accent_color,
                'splash_tagline': self.preview_tagline,
                'ai_brand_prompt': self.brand_description,
            })
        
        # Update system parameters
        IrConfigParam = self.env['ir.config_parameter'].sudo()
        IrConfigParam.set_param('pwa.theme_color', self.preview_theme_color)
        IrConfigParam.set_param('pwa.background_color', self.preview_background_color)
        IrConfigParam.set_param('pwa.primary_color', self.preview_primary_color)
        IrConfigParam.set_param('pwa.secondary_color', self.preview_secondary_color)
        IrConfigParam.set_param('pwa.accent_color', self.preview_accent_color)
        IrConfigParam.set_param('pwa.ai_brand_prompt', self.brand_description)
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('🎨 Branding Applied!'),
                'message': _('Your AI-generated PWA branding has been saved. Refresh your app to see the changes!'),
                'type': 'success',
                'sticky': False,
            }
        }

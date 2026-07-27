# -*- coding: utf-8 -*-
import logging
from urllib.parse import quote

from odoo import http, SUPERUSER_ID, _
from odoo.http import request
from odoo.addons.web.controllers.home import Home
from odoo.addons.web_auth_otp_login.controllers.main import WebAuthOtpController

_logger = logging.getLogger(__name__)


def _get_or_create_gst_company_partner(vat):
    """
    Search for or create a Company res.partner (is_company=True) for the given GST number.
    Auto-fetches address & company details using Odoo l10n_in TIN state lookup and enrich_by_gst if available.
    """
    vat_clean = (vat or '').strip().upper()
    if not vat_clean:
        return False

    Partner = request.env['res.partner'].with_user(SUPERUSER_ID)

    # 1. Look for existing company partner with this GSTIN
    company_partner = Partner.search([
        ('vat', '=ilike', vat_clean),
        ('is_company', '=', True),
    ], limit=1)

    if company_partner:
        return company_partner

    # 2. Extract state & country default from GSTIN prefix (first 2 digits)
    state_id = False
    country = request.env['res.country'].with_user(SUPERUSER_ID).search([('code', '=', 'IN')], limit=1)
    country_id = country.id if country else False

    if len(vat_clean) >= 2 and vat_clean[:2].isdigit():
        tin_code = vat_clean[:2]
        state = request.env['res.country.state'].with_user(SUPERUSER_ID).search([('l10n_in_tin', '=', tin_code)], limit=1)
        if state:
            state_id = state.id

    company_vals = {
        'name': f"Company ({vat_clean})",
        'is_company': True,
        'company_type': 'company',
        'vat': vat_clean,
        'country_id': country_id,
        'state_id': state_id,
        'x_skip_gst': False,
    }

    if 'l10n_in_gst_treatment' in Partner._fields:
        company_vals['l10n_in_gst_treatment'] = 'regular'

    # 3. Attempt GST IAP enrichment if available in Odoo environment
    try:
        if hasattr(Partner, '_l10n_in_get_partner_vals_by_vat'):
            enriched = Partner._l10n_in_get_partner_vals_by_vat(vat_clean)
            if enriched:
                for fname in ['name', 'street', 'street2', 'city', 'zip', 'state_id', 'country_id', 'l10n_in_gst_treatment']:
                    if enriched.get(fname):
                        company_vals[fname] = enriched[fname]
        elif hasattr(Partner, 'enrich_by_gst'):
            enriched = Partner.enrich_by_gst(vat_clean)
            if enriched and not enriched.get('error'):
                for fname in ['name', 'street', 'street2', 'city', 'zip']:
                    if enriched.get(fname):
                        company_vals[fname] = enriched[fname]
                if enriched.get('state_id') and isinstance(enriched['state_id'], dict):
                    company_vals['state_id'] = enriched['state_id'].get('id')
                if enriched.get('country_id') and isinstance(enriched['country_id'], dict):
                    company_vals['country_id'] = enriched['country_id'].get('id')
    except Exception as e:
        _logger.warning("GST auto-enrichment failed for VAT %s: %s", vat_clean, e)

    company_partner = Partner.create(company_vals)
    return company_partner


class SudiDiamondHome(Home):

    def _login_redirect(self, uid, redirect=None):
        redirect_url = super()._login_redirect(uid, redirect=redirect)
        if uid:
            user = request.env['res.users'].sudo().browse(uid)
            partner = user.partner_id
            if partner and not partner.vat and not partner.commercial_partner_id.vat and not partner.x_skip_gst:
                # Redirect to GST onboarding if GST is missing on both individual and commercial partner
                encoded_redirect = quote(redirect_url or '/jangad')
                return f'/web/gst_onboarding?redirect={encoded_redirect}'
        return redirect_url


class SudiDiamondAuthOtpController(WebAuthOtpController):

    @http.route('/web/auth/otp/verify', type='jsonrpc', auth='none', methods=['POST'], csrf=False)
    def verify_otp(self, phone, otp_code, redirect=None):
        res = super().verify_otp(phone, otp_code, redirect=redirect)
        if not res.get('success'):
            return res

        stored_user_id = request.session.get('otp_user_id')
        stored_partner_id = request.session.get('otp_partner_id')
        phone_clean = ''.join(c for c in phone if c.isdigit() or c == '+')

        # Check if this is an existing user or a new customer registration
        partner = request.env['res.partner'].sudo().browse(stored_partner_id) if stored_partner_id else False
        is_guest = partner and partner.name.startswith('Guest (')

        if stored_user_id or (partner and not is_guest):
            # Existing user authenticated in super()
            user = request.env.user if not request.env.user._is_public() else request.env['res.users'].sudo().browse(stored_user_id)
            user_partner = user.partner_id if user else partner
            target_redirect = redirect or '/jangad'
            if user_partner and not user_partner.vat and not user_partner.commercial_partner_id.vat and not user_partner.x_skip_gst:
                target_redirect = f'/web/gst_onboarding?redirect={quote(target_redirect)}'
            res['redirect'] = target_redirect
            return res

        # New customer registration required
        request.session['otp_verified_phone'] = phone_clean
        request.session['otp_verified_partner_id'] = stored_partner_id
        target_redirect = '/web/auth/otp/register'
        if redirect and redirect != '/jangad':
            target_redirect += f'?redirect={quote(redirect)}'

        return {
            'success': True,
            'registration_required': True,
            'message': _('OTP verified. Please complete customer registration.'),
            'redirect': target_redirect,
        }


class SudiDiamondGstOnboardingController(http.Controller):

    @http.route('/web/gst_onboarding', type='http', auth='user', website=True, sitemap=False, methods=['GET'])
    def gst_onboarding_form(self, **kwargs):
        partner = request.env.user.partner_id.sudo()
        redirect = kwargs.get('redirect') or '/jangad'
        if not redirect.startswith('/'):
            redirect = '/jangad'

        # If partner or its commercial parent already has GST or already opted to skip GST, bypass onboarding
        if partner.vat or partner.commercial_partner_id.vat or partner.x_skip_gst:
            return request.redirect(redirect)

        return request.render('diamond.gst_onboarding_page', {
            'csrf_token': request.csrf_token(),
            'redirect': redirect,
            'partner': partner,
            'error': kwargs.get('error'),
        })

    @http.route('/web/gst_onboarding/submit', type='http', auth='user', website=True, sitemap=False, methods=['POST'])
    def gst_onboarding_submit(self, **post):
        partner = request.env.user.partner_id.sudo()
        redirect = post.get('redirect') or '/jangad'
        if not redirect.startswith('/'):
            redirect = '/jangad'

        skip_gst = post.get('skip_gst') in ['1', 'true', 'True', True] or 'skip_button' in post
        vat = (post.get('vat') or '').strip()

        if skip_gst:
            partner.write({'x_skip_gst': True})
        elif vat:
            company_partner = _get_or_create_gst_company_partner(vat)
            partner_vals = {'vat': vat, 'x_skip_gst': False}
            if company_partner:
                partner_vals.update({
                    'parent_id': company_partner.id,
                    'is_company': False,
                    'company_type': 'person',
                })
            partner.write(partner_vals)
        else:
            return self.gst_onboarding_form(error=_('Please enter a GST Number or choose to skip for now.'), redirect=redirect)

        return request.redirect(redirect)


class SudiDiamondCustomerRegistrationController(http.Controller):

    @http.route('/web/auth/otp/register', type='http', auth='public', website=True, sitemap=False, methods=['GET'])
    def otp_register_form(self, **kwargs):
        phone = request.session.get('otp_verified_phone')
        if not phone:
            return request.redirect('/web/login?mode=otp')

        redirect = kwargs.get('redirect') or '/jangad'
        if not redirect.startswith('/'):
            redirect = '/jangad'

        return request.render('diamond.otp_new_customer_form', {
            'csrf_token': request.csrf_token(),
            'phone': phone,
            'redirect': redirect,
            'error': kwargs.get('error'),
        })

    @http.route('/web/auth/otp/register/submit', type='http', auth='public', website=True, sitemap=False, methods=['POST'])
    def otp_register_submit(self, **post):
        phone = request.session.get('otp_verified_phone')
        if not phone:
            return request.redirect('/web/login?mode=otp')

        name = (post.get('name') or '').strip()
        vat = (post.get('vat') or '').strip()
        skip_gst = post.get('skip_gst') in ['1', 'true', 'True', True] or not vat
        redirect = post.get('redirect') or '/jangad'
        if not redirect.startswith('/'):
            redirect = '/jangad'

        if not name:
            return self.otp_register_form(error=_('Customer Name is required.'), redirect=redirect)

        Partner = request.env['res.partner'].with_user(SUPERUSER_ID)
        User = request.env['res.users'].with_user(SUPERUSER_ID)

        # Build search domain for existing individual partner by phone
        partner_domain = [('phone', '=', phone)]
        if len(phone) >= 10:
            last_10 = phone[-10:]
            partner_domain = [('phone', 'like', last_10)]

        partner = Partner.search(partner_domain, limit=1)
        partner_vals = {
            'name': name,
            'phone': phone,
            'is_company': False,
            'company_type': 'person',
            'x_skip_gst': skip_gst,
        }

        if vat:
            company_partner = _get_or_create_gst_company_partner(vat)
            if company_partner:
                partner_vals.update({
                    'parent_id': company_partner.id,
                    'vat': vat,
                })

        if partner:
            partner.write(partner_vals)
        else:
            partner = Partner.create(partner_vals)

        # Find or create user
        user = User.search([('partner_id', '=', partner.id), ('active', '=', True)], limit=1)
        if not user:
            existing_login_user = User.search([('login', '=', phone)], limit=1)
            if existing_login_user:
                user = existing_login_user
                user.write({'partner_id': partner.id, 'name': name})
            else:
                portal_group = request.env.ref('base.group_portal')
                import uuid
                user = User.create({
                    'name': name,
                    'login': phone,
                    'partner_id': partner.id,
                    'group_ids': [(6, 0, [portal_group.id])],
                    'password': str(uuid.uuid4()),
                    'active': True,
                })

        # Auto-authenticate session
        request.session.uid = user.id
        request.session.login = user.login
        request.session.db = request.env.registry.db_name
        request.session.context = dict(user.env['res.users'].context_get())
        request.env.registry.clear_cache()
        request.session.session_token = user._compute_session_token(request.session.sid)
        request.session.should_rotate = True

        # Clear registration session state
        request.session.pop('otp_verified_phone', None)
        request.session.pop('otp_verified_partner_id', None)
        request.session.touch()

        return request.redirect(redirect)

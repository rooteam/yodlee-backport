import requests
import json
import datetime
import logging
import uuid
import re

from odoo import models, api, fields, SUPERUSER_ID
from odoo.exceptions import AccessError, UserError
from odoo.tools.translate import _

_logger = logging.getLogger(__name__)


class YodleeProviderAccountExt(models.Model):
    _inherit = ['account.online.provider']

    @api.model
    def _get_yodlee_credentials(self):
        ICP_obj = self.env['ir.config_parameter'].sudo()
        login = ICP_obj.get_param('yodlee_id') or self._cr.dbname
        secret = ICP_obj.get_param('yodlee_secret') or ICP_obj.get_param('database.uuid')
        url = ICP_obj.get_param('yodlee_service_url') or 'https://onlinesync.odoo.com/yodlee/api/2'
        fastlink_url = 'https://usyirestmasternode.yodleeinteractive.com/authenticate/odooinc/?channelAppName=usyirestmaster'
        return {'login': login, 'secret': secret, 'url': url, 'fastlink_url': fastlink_url}


    @api.multi
    def do_cobrand_login(self):
        credentials = self._get_yodlee_credentials()
        # requestBody = {'cobrandLogin': credentials['login'], 'cobrandPassword': credentials['secret']}
        requestBody = json.dumps({'cobrand': {'cobrandLogin': credentials['login'], 'cobrandPassword': credentials['secret']}})
        try:
            resp = requests.post(url=credentials['url']+'/cobrand/login', data=requestBody, timeout=30)
        except requests.exceptions.Timeout:
            raise UserError(_('Timeout: the server did not reply within 30s'))
        self.check_yodlee_error(resp)
        company_id = self.company_id or self.env.user.company_id
        company_id.yodlee_access_token = resp.json().get('session').get('cobSession')

    @api.multi
    def do_user_login(self):
        credentials = self._get_yodlee_credentials()
        company_id = self.company_id or self.env.user.company_id
        headerVal = {'Authorization': '{cobSession='+company_id.yodlee_access_token+'}'}
        # requestBody = {'loginName': company_id.yodlee_user_login, 'password': company_id.yodlee_user_password}
        requestBody = json.dumps({'user': {'loginName': company_id.yodlee_user_login, 'password': company_id.yodlee_user_password}})
        try:
            resp = requests.post(url=credentials['url']+'/user/login', data=requestBody, headers=headerVal, timeout=30)
        except requests.exceptions.Timeout:
            raise UserError(_('Timeout: the server did not reply within 30s'))
        self.check_yodlee_error(resp)
        company_id.yodlee_user_access_token = resp.json().get('user').get('session').get('userSession')

    @api.multi
    def get_login_form(self, site_id, provider):
        state = 'add'
        beta = False
        if provider != 'yodlee':
            return super(YodleeProviderAccount, self).get_login_form(site_id, provider)
        # resp_json = self.yodlee_fetch('/providers/'+str(site_id), {}, {}, 'GET')
        resp_json = self.yodlee_fetch('/user/accessTokens', {'appIds': '10003600'}, {}, 'GET')
        callbackUrl = '/sync_status/' + str(self.env.context.get('journal_id', 0)) + '/' + state
        paramsUrl = 'flow=%s&siteId=%s&callback=' if state == 'add' else 'flow=%s&siteAccountId=%s&callback='
        paramsUrl = paramsUrl % (state, site_id)
        if not resp_json:
            raise UserError(_('Could not retrieve login form for siteId: %s (%s)' % (site_id, provider)))
        return {
                'type': 'ir.actions.client',
                'tag': 'yodlee_online_sync_widget',
                'target': 'new',
                'fastlinkUrl': self._get_yodlee_credentials()['fastlink_url'],
                'login_form': resp_json,
                'context': self.env.context,
                'paramsUrl': paramsUrl,
                'callbackUrl': callbackUrl,
                'userToken': self.env.user.company_id.yodlee_user_access_token,
                'beta': beta,
                'state': state,
                'accessTokens': resp_json.get('user').get('accessTokens')[0],
                'context': self.env.context,
                }

    def _getStatus(self, status):
        if status == 1:
            return 'ACTION_ABANDONED'
        if status == 2:
            return 'SUCCESS'
        if status == 3:
            return 'FAILED'
        else:
            return status

    def callback_institution(self, informations, state, journal_id):
        action = self.env.ref('account.open_account_journal_dashboard_kanban')
        try:
            resp_json = json.loads(informations.get('JSONcallBackStatus', ''))
        except ValueError:
            raise UserError(_('Could not make sense of parameters: %s') % (informations,))
        element = type(resp_json) is list and len(resp_json) > 0 and resp_json[0] or {}
        if element.get('providerAccountId'):
            new_provider_account = self.search([('provider_account_identifier', '=', element.get('providerAccountId')),
                ('company_id', '=', self.env.company.id)], limit=1)
            if len(new_provider_account) == 0:
                vals = {
                    'name': element.get('bankName') or _('Online institution'),
                    'provider_account_identifier': element.get('providerAccountId'),
                    'provider_identifier': element.get('providerId'),
                    'status': self._getStatus(element.get('status')),
                    'status_code': element.get('code'),
                    'message': element.get('reason'),
                    'last_refresh': fields.Datetime.now(),
                    'action_required': False,
                    'provider_type': 'yodlee',
                }
                new_provider_account = self.create(vals)
                if element.get('status') == 'SUCCESS':
                    self.yodlee_fetch('/add_institution', {}, {'providerId': element.get('providerId')}, 'POST')
            else:
                new_provider_account.write({
                    'status': self._getStatus(element.get('status')),
                    'status_code': element.get('code'),
                    'message': element.get('reason'),
                    'last_refresh': fields.Datetime.now(),
                    'action_required': False if element.get('status') == 'SUCCESS' else True,
                })
                if self._getStatus(element.get('status')) == 'FAILED':
                    message = _('Error %s, message: %s') % (element.get('code'), element.get('reason'))
                    new_provider_account.log_message(message)
            # Fetch accounts
            res = new_provider_account.add_update_accounts()
            res.update({'status': self._getStatus(element.get('status')),
                'message': element.get('reason'),
                'method': state,
                'journal_id': journal_id})
            return self.show_result(res)
        else:
            return action

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

class OnlineAccountWizard(models.TransientModel):
    _inherit = 'account.online.wizard'

    transactions = fields.Html(readonly=True)
    status = fields.Selection([('success', 'Success'), ('failed', 'Failed'), ('cancelled', 'Cancelled')], readonly=True)
    method = fields.Selection([('add', 'add'), ('edit', 'edit'), ('refresh', 'refresh')], readonly=True)

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
            return super(YodleeProviderAccountExt, self).get_login_form(site_id, provider)
        return self.open_yodlee_action(site_id, state)
        

    def update_credentials(self):
        if self.provider_type != 'yodlee':
            return super(YodleeProviderAccountExt, self).update_credentials()
        self.ensure_one()
        return self.open_yodlee_action(self.provider_account_identifier, 'edit')

    # def manual_sync(self, return_action=True):
    #     if self.provider_type != 'yodlee':
    #         return super(YodleeProviderAccountExt, self).manual_sync()
    #     self.ensure_one()
    #     return self.open_yodlee_action(self.provider_account_identifier, 'refresh')

    def open_yodlee_action(self, identifier, state, beta=False):
        resp_json = self.yodlee_fetch('/user/accessTokens', {'appIds': '10003600'}, {}, 'GET')
        callbackUrl = '/sync_status/' + str(self.env.context.get('journal_id', 0)) + '/' + state
        paramsUrl = 'flow=%s&siteId=%s&callback=' if state == 'add' else 'flow=%s&siteAccountId=%s&callback='
        paramsUrl = paramsUrl % (state, identifier)
        # if state == 'add' and not resp_json:
        #     raise UserError(_('Could not retrieve login form for siteId: %s (%s)' % (site_id, provider)))
        return {
                'type': 'ir.actions.client',
                'tag': 'yodlee_online_sync_widget',
                'target': 'new',
                'fastlinkUrl': self._get_yodlee_credentials()['fastlink_url'],
                'paramsUrl': paramsUrl,
                'callbackUrl': callbackUrl,
                'userToken': self.env.user.company_id.yodlee_user_access_token,
                'beta': beta,
                'state': state,
                'accessTokens': resp_json.get('user').get('accessTokens')[0],
                'context': self.env.context,
                'login_form': resp_json
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
        action = self.env.ref('account.open_account_journal_dashboard_kanban').id
        try:
            resp_json = json.loads(informations.get('JSONcallBackStatus', ''))
        except ValueError:
            raise UserError(_('Could not make sense of parameters: %s') % (informations,))
        element = type(resp_json) is list and len(resp_json) > 0 and resp_json[0] or {}
        if element.get('providerAccountId'):
            new_provider_account = self.search([('provider_account_identifier', '=', element.get('providerAccountId')),
                ('company_id', '=', self.env.user.company_id.id)], limit=1)
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


    def show_result(self, values):
        """ This method is used to launch the end process of adding/refreshing/editing an online account provider
            It will create a wizard where user will be notified of the result of the call and if new accounts have
            been fetched, he will be able to link them to different journals
        """
        number_added = len(values.get('added', []))
        status = 'success'
        if values.get('status') == 'FAILED' or values.get('status') == '3':
            status = 'failed'
        if values.get('status') == 'ACTION_ABANDONED' or values.get('status') == '1':
            status = 'cancelled'
        if values.get('transactions'):
            transactions = "<br/><br/><p>%s</p>" % (_('The following transactions have been loaded in the system.'),)
            for tr in values.get('transactions'):
                transactions += '<br/><p>%s: <strong>%s</strong> - %s %s</p>' % (_('Journal'), tr.get('journal'), tr.get('count'), _('transactions loaded'))
        else:
            transactions = '<br/><br/><p>%s</p>' % (_('No new transactions have been loaded in the system.'),)
        hide_table = False
        if number_added == 0:
            hide_table = True
        transient = self.env['account.online.wizard'].create({
            'number_added': number_added,
            'status': status,
            'method': values.get('method'),
            'message': values.get('message', _('Unknown reason')),
            'transactions': transactions,
            'hide_table': hide_table,
        })
        for account in values.get('added', []):
            vals = {'online_account_id': account.id, 'account_online_wizard_id': transient.id}
            # If we are adding an account for the first time and only have one, link it to journal
            if (number_added == 1 and values.get('method') == 'add'):
                vals['journal_id'] = values.get('journal_id')
            self.env['account.online.link.wizard'].create(vals)

        action = self.env.ref('account_online_sync.action_account_online_wizard_form').read()[0]
        action['res_id'] = transient.id
        return action['id']


    @api.multi
    def add_update_accounts(self):
        params = {'providerAccountId': self.provider_account_identifier}
        resp_json = self.yodlee_fetch('/accounts/', params, {}, 'GET')
        accounts = resp_json.get('account', [])
        accounts_added = self.env['account.online.journal']
        transactions = []
        for account in accounts:
            if account.get('CONTAINER') in ('bank', 'creditCard'):
                vals = {
                    'yodlee_account_status': account.get('accountStatus'),
                    'yodlee_status_code': account.get('refreshinfo', {}).get('statusCode'),
                    #'balance': account.get('currentBalance', {}).get('amount', 0) if account.get('CONTAINER') == 'bank' else account.get('runningBalance', {}).get('amount', 0)
                    #Updated similar to v13 module
                    'balance': account.get('currentBalance', account.get('balance', {})).get('amount', 0) if account.get('CONTAINER') == 'bank' else account.get('runningBalance', {}).get('amount', 0)
                }
                account_search = self.env['account.online.journal'].search([('account_online_provider_id', '=', self.id), ('online_identifier', '=', account.get('id'))], limit=1)
                if len(account_search) == 0:
                    dt = datetime.datetime
                    # Since we just create account, set last sync to 15 days in the past to retrieve transaction from latest 15 days
                    last_sync = dt.strftime(dt.strptime(self.last_refresh, DEFAULT_SERVER_DATETIME_FORMAT) - datetime.timedelta(days=15), DEFAULT_SERVER_DATE_FORMAT)
                    vals.update({
                        'name': account.get('accountName', 'Account'),
                        'online_identifier': account.get('id'),
                        'account_online_provider_id': self.id,
                        'account_number': account.get('accountNumber'),
                        'last_sync': last_sync,
                    })
                    with self.pool.cursor() as cr:
                        acc = self.with_env(self.env(cr=cr)).env['account.online.journal'].create(vals)
                    accounts_added += acc
                else:
                    with self.pool.cursor() as cr:
                        account_search.with_env(self.env(cr=cr)).env['account.online.journal'].write(vals)
                    # Also retrieve transaction if status is SUCCESS
                    if vals.get('yodlee_status_code') == 0 and account_search.journal_ids:
                        transactions_count = account_search.retrieve_transactions()
                        transactions.append({'journal': account_search.journal_ids[0].name, 'count': transactions_count})
        return {'accounts_added': accounts_added, 'transactions': transactions}
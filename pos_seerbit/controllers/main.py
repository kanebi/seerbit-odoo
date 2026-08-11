# coding: utf-8
import json
import logging
import pprint

from odoo import http
from odoo.http import Response, request
from werkzeug.exceptions import Forbidden

_logger = logging.getLogger(__name__)


class SeerbitController(http.Controller):
    @http.route('/pos_seerbit/notification', type='json', auth='public', methods=['POST'], csrf=False)
    def seerbit_notification(self, **kwargs):
        '''
        This is the webhook intended for listening to only Seerbit notifications
        It is understandable that a bad actor can choose post a fake "Seerbit-like"
            notification to this endpoint and in turn leads to wrong validation,
            as such, received notifications needs to be re-verified.
        '''
        data = json.loads(http.request.httprequest.data)
        # Ignore unknown ill-formed data
        try:
            notification = data.get('notificationItems')[0]["notificationRequestItem"]
            # ignore none transaction notification
            if notification["eventType"] != "transaction":
                return
            payment_method = http.request.env['pos.payment.method'].sudo().browse()
            public_key = notification["data"].get("publicKey")
            company = http.request.env['res.company'].sudo().seerbit_company_by_public_key(public_key)
            if not company and public_key:
                # Ambiguous or missing — try terminalId if present on a Seerbit PM
                terminal_id = notification["data"].get("terminalId") or notification["data"].get("deviceId")
                if terminal_id:
                    payment_method = http.request.env['pos.payment.method'].sudo().search([
                        ('use_payment_terminal', '=', 'seerbit'),
                        ('seerbit_terminal_id', '=', terminal_id),
                    ], limit=1)
                    company = payment_method.company_id
            if company and not payment_method:
                payment_method = http.request.env['pos.payment.method'].sudo().search([
                    ('company_id', '=', company.id),
                    ('use_payment_terminal', '=', 'seerbit'),
                ], limit=1)

            if payment_method:
                if notification["data"]["code"] == "00":
                    payment_method.seerbit_latest_response = json.dumps(notification)
                    _logger.info('A payment notification has been saved')
                else:
                    _logger.info(
                        'A non-approved notification received from Seerbit for transaction: %s',
                        notification.get("data", {}).get("transactionRef", "unknown"),
                    )
            else:
                _logger.error(
                    'Received a message with an invalid/ambiguous public key for transaction: %s',
                    notification.get("data", {}).get("transactionRef", "unknown"),
                )
        
        except Exception as e:
            _logger.error(
                "Error processing reconciliation notification: %s", str(e))
            return {'status': 'error', 'message': f'Processing error: {str(e)}'}



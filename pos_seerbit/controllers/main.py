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
            payment_method = http.request.env['pos.payment.method'].sudo().search(
                [('seerbit_public_key', '=', notification["data"]["publicKey"])], limit=1)

            if payment_method:
                # This notification is valid
                if (notification["data"]["code"] == "00"):# and
                    #self.is_verified(notification)):
                    payment_method.seerbit_latest_response = json.dumps(notification)
                    _logger.info('A payment notification has been saved')
                else:
                    _logger.info('A non-approved notification received from Seerbit for transaction: %s',
                                notification.get("data", {}).get("transactionRef", "unknown"))
            else:
                _logger.error('Received a message with an invalid public key for transaction: %s',
                            notification.get("data", {}).get("transactionRef", "unknown"))
        
        except Exception as e:
            _logger.error(
                "Error processing reconciliation notification: %s", str(e))
            return {'status': 'error', 'message': f'Processing error: {str(e)}'}



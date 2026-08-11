import requests
import logging
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

class SeerbitAPI:
    def __init__(self, env, company=None):
        self.env = env
        self.company = company or env.company
        if not self.company:
            raise UserError("Seerbit API requires a company context.")

        company = self.company.sudo()
        self.secret_key = company.seerbit_secret_key or ''
        self.public_key = company.seerbit_public_key or ''
        self._encrypted_key_param = f'pos_seerbit.seerbit_encrypted_key.{company.id}'

        self._encrypted_key = None

        if not self.secret_key or not self.public_key:
            _logger.warning(
                "Seerbit keys missing for company %s (%s).",
                company.name,
                company.id,
            )

    def _get_encrypted_key(self, force_refresh=False):
        if self._encrypted_key and not force_refresh:
            return self._encrypted_key

        param_obj = self.env['ir.config_parameter'].sudo()
        if not force_refresh:
            cached_key = param_obj.get_param(self._encrypted_key_param)
            if cached_key:
                self._encrypted_key = cached_key
                return cached_key

        if not self.secret_key or not self.public_key:
             raise UserError(
                 "Seerbit keys are missing for %s. Configure them in Settings → Seerbit for that company."
                 % self.company.name
             )

        url = 'https://seerbitapi.com/api/v2/encrypt/keys'
        payload = {
            "key": f"{self.secret_key}.{self.public_key}"
        }
        try:
            response = requests.post(url, json=payload, headers={'Content-Type': 'application/json'}, timeout=5)
            response.raise_for_status()
            res_data = response.json()
            if res_data.get('status') == 'SUCCESS' and 'data' in res_data:
                data = res_data['data']
                if 'EncryptedSecKey' in data and 'encryptedKey' in data['EncryptedSecKey']:
                    self._encrypted_key = data['EncryptedSecKey']['encryptedKey']
                    param_obj.set_param(self._encrypted_key_param, self._encrypted_key)
                    return self._encrypted_key
            raise UserError("Failed to parse encrypted key from Seerbit response")
        except Exception as e:
            _logger.error(f"Seerbit Encrypt Key Error: {e}")
            if not force_refresh:
                cached_key = param_obj.get_param(self._encrypted_key_param)
                if cached_key:
                    self._encrypted_key = cached_key
                    return cached_key
            raise UserError(f"Failed to authenticate with Seerbit: {str(e)}")

    def _get_headers(self):
        return {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self._get_encrypted_key()}'
        }

    def _do_request(self, method, url, **kwargs):
        if 'timeout' not in kwargs:
            kwargs['timeout'] = 10
            
        headers = kwargs.get('headers')
        if not headers:
            headers = self._get_headers()
            kwargs['headers'] = headers
            
        try:
            response = requests.request(method, url, **kwargs)
            if response.status_code == 401:
                _logger.info("Seerbit request returned 401. Attempting to refresh token...")
                self._get_encrypted_key(force_refresh=True)
                if 'PublicKey' in kwargs['headers']:
                    kwargs['headers']['Authorization'] = f'Bearer {self._get_encrypted_key()}'
                else:
                    kwargs['headers'] = self._get_headers()
                response = requests.request(method, url, **kwargs)
                
            return response
        except Exception as e:
            _logger.error("Seerbit request failed [%s %s]: %s", method, url, str(e))
            raise

    @staticmethod
    def send_pos_fallback(payload):
        """
        Sends a payload to the POS notification fallback endpoint.
        Does not require authentication.
        """
        url = "https://posnotification.seerbitapi.com/"
        try:
            headers = {"Content-Type": "application/json"}
            response = requests.post(url, json=payload, headers=headers, timeout=30)

            if response.status_code in (200, 201):
                _logger.info("Payload sent to fallback endpoint successfully: %s", payload.get('id'))
                return True
            else:
                _logger.warning("Fallback endpoint returned error %s: %s", response.status_code, response.text)
                return False
        except requests.exceptions.ReadTimeout:
            _logger.warning("Fallback endpoint timed out for transaction ID: %s", payload.get('id'))
            return False
        except Exception as e:
            _logger.warning("Failed to send to fallback endpoint: %s", str(e))
            return False

    # Virtual Accounts
    def create_virtual_account(self, full_name, reference, email, currency="NGN", country="NG"):
        url = 'https://seerbitapi.com/api/v2/virtual-accounts'
        payload = {
            "publicKey": self.public_key,
            "fullName": full_name,
            "bankVerificationNumber": "",
            "currency": currency,
            "country": country,
            "reference": reference,
            "email": email
        }
        
        try:
            response = self._do_request('POST', url, json=payload)
            _logger.info("Seerbit HTTPS Response [POST %s]: Status %s - Body: %s", url, response.status_code, response.text)
            response.raise_for_status()
            res_data = response.json()
            if res_data.get('status') == 'SUCCESS' and 'data' in res_data:
                return res_data['data']
            raise UserError(f"Seerbit Error: {res_data.get('message', 'Unknown Error')}")
        except UserError:
            raise
        except Exception as e:
            _logger.error(f"Seerbit Create VA Error: {e}")
            raise UserError(f"Failed to create Virtual Account: {str(e)}")

    def delete_virtual_account(self, reference):
        url = f'https://seerbitapi.com/api/v2/virtual-accounts/{reference}'
        try:
            response = self._do_request('DELETE', url)
            _logger.info("Seerbit HTTPS Response [DELETE %s]: Status %s - Body: %s", url, response.status_code, response.text)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            _logger.error(f"Seerbit Delete VA Error: {e}")
            return False

    def get_virtual_account_payments(self, account_number):
        url = f'https://seerbitapi.com/api/v2/virtual-accounts/{self.public_key}/{account_number}'
        try:
            response = self._do_request('GET', url)
            _logger.info("Seerbit HTTPS Response [GET %s]: Status %s - Body: %s", url, response.status_code, response.text)
            response.raise_for_status()
            res_data = response.json()
            if res_data.get('status') == 'SUCCESS' and 'data' in res_data:
                return res_data['data'].get('payload', [])
            return []
        except Exception as e:
            _logger.error(f"Seerbit Get VA Payments Error: {e}")
            raise UserError(f"Failed to fetch VA payments: {str(e)}")

    # Invoicing
    def create_invoice(self, order_no, due_date, currency, receivers_name, customer_email, invoice_items):
        url = 'https://merchant.seerbitapi.com/invoice/create'
        payload = {
            "publicKey": self.public_key,
            "orderNo": order_no,
            "dueDate": due_date,
            "currency": currency,
            "receiversName": receivers_name,
            "customerEmail": customer_email,
            "invoiceItems": invoice_items
        }
        
        try:
            response = self._do_request('POST', url, json=payload)
            _logger.info("Seerbit HTTPS Response [POST %s]: Status %s - Body: %s", url, response.status_code, response.text)
            response.raise_for_status()
            res_data = response.json()
            if res_data.get('code') == '00':
                return res_data['payload']
            raise UserError(f"Seerbit Error: {res_data.get('message', 'Unknown Error')}")
        except UserError:
            raise
        except Exception as e:
            _logger.error(f"Seerbit Create Invoice Error: {e}")
            raise UserError(f"Failed to create Seerbit Invoice: {str(e)}")

    def get_invoice(self, invoice_no):
        url = f'https://merchant.seerbitapi.com/invoice/{self.public_key}/{invoice_no}'
        try:
            response = self._do_request('GET', url)
            _logger.info("Seerbit HTTPS Response [GET %s]: Status %s - Body: %s", url, response.status_code, response.text)
            response.raise_for_status()
            res_data = response.json()
            if res_data.get('code') == '00':
                return res_data['payload']
            return False
        except Exception as e:
            _logger.error(f"Seerbit Get Invoice Error: {e}")
            return None

    def send_invoice(self, invoice_no):
        """
        Triggers Seerbit to send the invoice to the customer's email.
        """
        if not self.public_key:
            raise UserError("Seerbit Public Key is not configured. Please check your POS settings.")
            
        url = f"https://merchant.seerbitapi.com/invoice/{self.public_key}/send/{invoice_no}"
        
        try:
            response = self._do_request('GET', url)
            _logger.info("Seerbit HTTPS Response [GET %s]: Status %s - Body: %s", url, response.status_code, response.text)
            
            response.raise_for_status()
            res_data = response.json()
            
            if res_data.get('status') == 'SUCCESS' or str(res_data.get('code')) in ('00', '200'):
                return True
                
            raise UserError(f"Seerbit Error: {res_data.get('message', 'Unknown Error')}")
            
        except UserError:
            raise
        except Exception as e:
            _logger.error(f"Seerbit Send Invoice Error: {e}")
            raise UserError(f"Failed to send Seerbit Invoice: {str(e)}")

    def delete_invoice(self, invoice_no):
        url = f'https://merchant.seerbitapi.com/invoice/{self.public_key}/{invoice_no}'
        try:
            response = self._do_request('DELETE', url)
            _logger.info("Seerbit HTTPS Response [DELETE %s]: Status %s - Body: %s", url, response.status_code, response.text)
            response.raise_for_status()
            return True
        except Exception as e:
            _logger.warning(f"Seerbit Delete Invoice Error: {e}")
            return False

    # Payment Links
    def create_payment_link(self, amount, currency, email, description, paymentLinkName, oneTime=True):
        import uuid
        url = 'https://paymentlink.seerbitapi.com/paymentlink/v2/payLinks/api'
        payload = {
            "status": "ACTIVE",
            "paymentLinkName": paymentLinkName,
            "description": description,
            "currency": currency,
            "amount": str(amount),
            "successMessage": "Thank you for your payment",
            "publicKey": self.public_key,
            "customizationName": f"Odoo-{uuid.uuid4().hex[:8]}",
            "paymentFrequency": "ONE_TIME",
            "email": email,
            "requiredFields": {
                "address": False,
                "amount": True,
                "customerName": True,
                "mobileNumber": False,
                "invoiceNumber": False
            },
            "linkExpirable": False,
            "oneTime": oneTime
        }
        
        try:
            response = self._do_request('POST', url, json=payload)
            _logger.info("Seerbit HTTPS Response [POST %s]: Status %s - Body: %s", url, response.status_code, response.text)
            response.raise_for_status()
            res_data = response.json()
            if res_data.get('data') and 'paymentLinks' in res_data['data']:
                return res_data['data']['paymentLinks']
            raise UserError(f"Seerbit Error: {res_data.get('message', 'Unknown Error')}")
        except UserError:
            raise
        except Exception as e:
            _logger.error(f"Seerbit Create Payment Link Error: {e}")
            raise UserError(f"Failed to create Seerbit Payment Link: {str(e)}")

    def update_payment_link(self, payment_link_id, amount, currency, email, description, paymentLinkName, oneTime=True):
        import uuid
        url = 'https://paymentlink.seerbitapi.com/paymentlink/v2/payLinks/api'
        payload = {
            "paymentLinkId": payment_link_id,
            "status": "ACTIVE",
            "paymentLinkName": paymentLinkName,
            "description": description,
            "currency": currency,
            "amount": str(amount),
            "successMessage": "Thank you for your payment",
            "publicKey": self.public_key,
            "customizationName": f"Odoo-{uuid.uuid4().hex[:8]}",
            "paymentFrequency": "ONE_TIME",
            "email": email,
            "requiredFields": {
                "address": False,
                "amount": True,
                "customerName": True,
                "mobileNumber": False,
                "invoiceNumber": False
            },
            "linkExpirable": False,
            "oneTime": oneTime
        }
        
        try:
            response = self._do_request('PUT', url, json=payload)
            _logger.info("Seerbit HTTPS Response [PUT %s]: Status %s - Body: %s", url, response.status_code, response.text)
            response.raise_for_status()
            res_data = response.json()
            if res_data.get('status') == 'SUCCESS' or str(res_data.get('code')) in ('00', '200'):
                return True
            raise UserError(f"Seerbit Error: {res_data.get('message', 'Unknown Error')}")
        except UserError:
            raise
        except Exception as e:
            _logger.error(f"Seerbit Update Payment Link Error: {e}")
            raise UserError(f"Failed to update Seerbit Payment Link: {str(e)}")

    def delete_payment_link(self, payment_link_id):
        url = f'https://paymentlink.seerbitapi.com/paymentlink/v2/payLinks/api/deleteLink/{payment_link_id}'
        try:
            response = self._do_request('DELETE', url)
            _logger.info("Seerbit HTTPS Response [DELETE %s]: Status %s - Body: %s", url, response.status_code, response.text)
            response.raise_for_status()
            res_data = response.json()
            if res_data.get('status') == 'Deleted':
                return True
            raise UserError(f"Seerbit Error: {res_data.get('message', 'Unknown Error')}")
        except UserError:
            raise
        except Exception as e:
            _logger.error(f"Seerbit Delete Payment Link Error: {e}")
            raise UserError(f"Failed to delete Seerbit Payment Link: {str(e)}")

# -*- coding: utf-8 -*-
import logging
import requests
from functools import wraps
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

def require_seerbit_auth(func):
    """
    Decorator to wrap Odoo model methods that require Seerbit Pocket API authentication.
    If a pocket API call fails due to invalid/missing tokens, this raises a specific
    UserError that the frontend can catch to trigger the Auth Modal.
    """
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        # We can optionally proactively check if token exists here
        # token = self.env['ir.config_parameter'].sudo().get_param('pos_seerbit.pocket_bearer_token')
        # if not token:
        #     raise UserError("POCKET_AUTH_REQUIRED")
        try:
            return func(self, *args, **kwargs)
        except UserError as e:
            if "POCKET_AUTH_REQUIRED" in str(e) or "Invalid token" in str(e):
                raise UserError("POCKET_AUTH_REQUIRED")
            raise
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code in (400, 401):
                res_text = e.response.text
                if "Invalid token" in res_text or "token" in res_text.lower():
                    raise UserError("POCKET_AUTH_REQUIRED")
            raise
    return wrapper

class SeerbitPocketAPI:
    """
    Client for Seerbit Pocket APIs (Payouts, Balances, etc).
    Authenticates via email/password to get a Bearer token.
    """
    def __init__(self, env):
        self.env = env
        self.public_key = self.env['ir.config_parameter'].sudo().get_param('pos_seerbit.seerbit_public_key')
        self.timeout = 60

    def _get_bearer_token(self):
        token = self.env['ir.config_parameter'].sudo().get_param('pos_seerbit.pocket_bearer_token')
        if not token:
            raise UserError("POCKET_AUTH_REQUIRED")
        return token

    def _do_request(self, method, url, **kwargs):
        kwargs.setdefault('timeout', self.timeout)
        try:
            response = requests.request(method, url, **kwargs)
            if response.status_code in (400, 401):
                try:
                    res_data = response.json()
                    msg = res_data.get("message", "")
                    if "Invalid token" in msg or "Expired token" in msg or msg == "Unauthorized" or "token" in msg.lower():
                        raise UserError("POCKET_AUTH_REQUIRED")
                except ValueError:
                    pass
            return response
        except requests.exceptions.RequestException as e:
            _logger.error(f"Seerbit Pocket API Request Failed: {e}")
            raise

    def authenticate(self, email, password):
        url = 'https://pocket.seerbitapi.com/pocket/authenticate'
        payload = {
            "email": email,
            "password": password
        }
        headers = {
            'Content-Type': 'application/json'
        }
        try:
            response = self._do_request('POST', url, json=payload, headers=headers)
            response.raise_for_status()
            res_data = response.json()
            if str(res_data.get('responseCode')) == '00' or res_data.get('message') == 'Success':
                data = res_data.get('data', {})
                token = data.get('bearerToken')
                if token:
                    self.env['ir.config_parameter'].sudo().set_param('pos_seerbit.pocket_bearer_token', token)
                return data
            raise UserError(f"Seerbit Pocket Auth Failed: {res_data.get('message', 'Unknown Error')}")
        except UserError:
            raise
        except Exception as e:
            _logger.error(f"Seerbit Pocket Auth Error: {e}")
            if hasattr(e, 'response') and e.response is not None:
                _logger.error(f"Seerbit Pocket Auth API Error Body: {e.response.text}")
            raise UserError(f"Failed to authenticate with Seerbit Pocket: {str(e)}")

    def get_pocket_balance(self):
        pocket_id = self.env['ir.config_parameter'].sudo().get_param('pos_seerbit.seerbit_pocket_id')
        if not pocket_id:
            raise UserError("Seerbit Pocket ID is not configured. Please check your POS Settings.")
            
        url = f'https://pocket.seerbitapi.com/pocket/balance/pocket-id/{pocket_id}'
        try:
            headers = {
                'Authorization': f'Bearer {self._get_bearer_token()}',
                'Public-Key': self.public_key,
                'Content-Type': 'application/json'
            }
            response = self._do_request('GET', url, headers=headers)
            response.raise_for_status()
            res_data = response.json()
            _logger.info("Seerbit Pocket Balance Response: %s", res_data)
            if (res_data.get('status') == 'SUCCESS' or str(res_data.get('responseCode')) == '00') and 'data' in res_data:
                data = res_data['data']
                balance = data.get('availableBalanceAmount') or data.get('balance', 0.0)
                return float(balance)
            raise UserError(f"Seerbit Error: {res_data.get('message', 'Unknown Error')}")
        except UserError:
            raise
        except Exception as e:
            _logger.error(f"Seerbit Get Pocket Balance Error: {e}")
            if hasattr(e, 'response') and e.response is not None:
                _logger.error(f"Seerbit Error Body: {e.response.text}")
            return 0.0

    def get_banks(self):
        url = 'https://pocket.seerbitapi.com/pocket/banks/category/NIP'
        try:
            headers = {
                'Authorization': f'Bearer {self._get_bearer_token()}',
                'Public-Key': self.public_key,
                'Content-Type': 'application/json'
            }
            response = self._do_request('GET', url, headers=headers)
            response.raise_for_status()
            res_data = response.json()
            if 'data' in res_data and 'banks' in res_data['data']:
                return res_data['data']['banks']
            elif 'content' in res_data:
                return res_data['content']
            return []
        except UserError:
            raise
        except Exception as e:
            _logger.error(f"Seerbit Get Banks Error: {e}")
            if hasattr(e, 'response') and e.response is not None:
                _logger.error(f"Seerbit Error Body: {e.response.text}")
            return []

    def account_enquiry(self, account_number, bank_code):
        url = 'https://pocket.seerbitapi.com/pocket/payout/account-enquiry'
        payload = {
            "accountnumber": account_number,
            "bankcode": bank_code
        }
        try:
            headers = {
                'Authorization': f'Bearer {self._get_bearer_token()}',
                'Public-Key': self.public_key,
                'Content-Type': 'application/json'
            }
            response = self._do_request('POST', url, json=payload, headers=headers)
            response.raise_for_status()
            res_data = response.json()
            _logger.info("Seerbit Pocket Enquiry Response: %s", res_data)
            if (res_data.get('status') == 'SUCCESS' or str(res_data.get('responseCode')) == '00') and 'data' in res_data:
                return res_data['data']
            return False
        except UserError:
            raise
        except Exception as e:
            _logger.error(f"Seerbit Account Enquiry Error: {e}")
            return False

    def get_otp(self):
        pocket_id = self.env['ir.config_parameter'].sudo().get_param('pos_seerbit.seerbit_pocket_id')
        if not pocket_id:
            raise UserError("Seerbit Pocket ID is not configured. Please check your POS Settings.")
            
        url = 'https://pocket.seerbitapi.com/pocket/getOtp'
        payload = {
            "actionItem": "APPROVE_DISBURSEMENT",
            "pocketId": pocket_id
        }
        try:
            headers = {
                'Authorization': f'Bearer {self._get_bearer_token()}',
                'Public-Key': self.public_key,
                'Content-Type': 'application/json'
            }
            response = self._do_request('POST', url, json=payload, headers=headers)
            response.raise_for_status()
            res_data = response.json()
            if res_data.get('status') == 'SUCCESS' or str(res_data.get('responseCode')) == '00':
                return True
            raise UserError(f"Seerbit OTP Error: {res_data.get('message', 'Failed to generate OTP')}")
        except UserError:
            raise
        except Exception as e:
            _logger.error(f"Seerbit Get OTP Error: {e}")
            raise UserError(f"Failed to generate OTP: {str(e)}")

    def get_signature(self, reference, amount, currency, description, account_number, bank_code, passkey):
        url = 'https://pocket.seerbitapi.com/pocket/payout/get-signature'
        payload = {
            "reference": reference,
            "amount": str(amount),
            "currency": currency,
            "description": description,
            "accountNumber": account_number,
            "bankCode": bank_code,
            "actionType": "APPROVE_DISBURSEMENT",
            "passKey": passkey
        }
        headers = {
            'Authorization': f'Bearer {self._get_bearer_token()}',
            'Content-Type': 'application/json'
        }
        try:
            response = self._do_request('POST', url, json=payload, headers=headers)
            response.raise_for_status()
            res_data = response.json()
            if str(res_data.get('responseCode')) == '00':
                sig_data = res_data.get('data')
                if isinstance(sig_data, dict) and 'signature' in sig_data:
                    return sig_data['signature']
                return str(sig_data).strip()
        except UserError:
            raise
        except Exception as e:
            error_msg = str(e)
            if hasattr(e, 'response') and e.response is not None:
                try:
                    res_data = e.response.json()
                    if 'message' in res_data:
                        error_msg = res_data['message']
                except Exception:
                    pass
            raise UserError(f"Seerbit Signature Error: {error_msg}")

    def submit_payout(self, amount, bank_code, account_number, account_name, reference, passkey, currency="NGN", description="Payment"):
        pocket_id = self.env['ir.config_parameter'].sudo().get_param('pos_seerbit.seerbit_pocket_id')
        if not pocket_id:
            raise UserError("Seerbit Pocket ID is not configured. Please check your POS Settings.")
        
        passkey = str(passkey).strip() if passkey else passkey
        amount_str = str(int(amount)) if float(amount).is_integer() else str(amount)
        
        # 1. Get Signature
        signature = self.get_signature(reference, amount_str, currency, description, account_number, bank_code, passkey)
        
        # 2. Initiate Payout
        url = f'https://pocket.seerbitapi.com/pocket/payout/encrypted/pocket-id/{pocket_id}'
        payload = {
            "reference": reference,
            "amount": amount_str,
            "currency": currency,
            "description": description,
            "accountNumber": account_number,
            "bankCode": bank_code,
            "passKey": passkey,
            "actionType": "APPROVE_DISBURSEMENT",
            "signature": signature
        }
        try:
            _logger.info("Seerbit Submit Payout Payload: %s", payload)
            headers = {
                'Authorization': f'Bearer {self._get_bearer_token()}',
                'Public-Key': self.public_key,
                'Content-Type': 'application/json'
            }
            response = self._do_request('POST', url, json=payload, headers=headers)
            response.raise_for_status()
            res_data = response.json()
            if res_data.get('status') == 'SUCCESS' or str(res_data.get('responseCode')) == '00':
                return res_data
            raise UserError(f"Seerbit Payout Error: {res_data.get('message', 'Failed to process payout')}")
        except UserError:
            raise
        except Exception as e:
            _logger.error(f"Seerbit Submit Payout Error: {e}")
            error_msg = str(e)
            if hasattr(e, 'response') and e.response is not None:
                _logger.error(f"Seerbit Error Body: {e.response.text}")
                try:
                    res_data = e.response.json()
                    if 'message' in res_data:
                        error_msg = res_data['message']
                except Exception:
                    pass
            raise UserError(f"Seerbit Payout Error: {error_msg}")

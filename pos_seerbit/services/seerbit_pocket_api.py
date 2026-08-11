# -*- coding: utf-8 -*-
import logging

import requests

from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


def require_seerbit_auth(func):
    """Raise POCKET_AUTH_REQUIRED when Pocket API token is missing or invalid."""
    def wrapper(self, *args, **kwargs):
        try:
            return func(self, *args, **kwargs)
        except UserError as exc:
            if "POCKET_AUTH_REQUIRED" in str(exc) or "Invalid token" in str(exc):
                raise UserError("POCKET_AUTH_REQUIRED") from exc
            raise
        except requests.exceptions.HTTPError as exc:
            if exc.response is not None and exc.response.status_code in (400, 401):
                res_text = exc.response.text
                if "Invalid token" in res_text or "token" in res_text.lower():
                    raise UserError("POCKET_AUTH_REQUIRED") from exc
            raise
    return wrapper


class SeerbitPocketAPI:
    """Seerbit Pocket API client — credentials from res.company business configuration."""

    def __init__(self, env, company=None):
        self.env = env
        self.company = env['res.company'].seerbit_company(company)
        self.public_key = self.company.seerbit_public_key
        self.pocket_id = self.company.seerbit_pocket_id
        self.timeout = 60

    def _bearer_token_param_key(self):
        return self.company.seerbit_pocket_bearer_param_key()

    def _get_bearer_token(self):
        token = self.env['ir.config_parameter'].sudo().get_param(self._bearer_token_param_key())
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
                    if (
                        "Invalid token" in msg
                        or "Expired token" in msg
                        or msg == "Unauthorized"
                        or "token" in msg.lower()
                    ):
                        raise UserError("POCKET_AUTH_REQUIRED")
                except ValueError:
                    pass
            return response
        except requests.exceptions.RequestException as exc:
            _logger.error("Seerbit Pocket API request failed: %s", exc)
            raise

    def authenticate(self, email=None, password=None):
        email = email or self.company.seerbit_pocket_email
        password = password or self.company.seerbit_pocket_password
        if not email or not password:
            raise UserError(
                "Configure Seerbit Pocket Email and Password for %s in Settings → Seerbit."
                % self.company.name
            )
        url = 'https://pocket.seerbitapi.com/pocket/authenticate'
        payload = {'email': email, 'password': password}
        headers = {'Content-Type': 'application/json'}
        try:
            response = self._do_request('POST', url, json=payload, headers=headers)
            response.raise_for_status()
            res_data = response.json()
            if str(res_data.get('responseCode')) == '00' or res_data.get('message') == 'Success':
                data = res_data.get('data', {})
                token = data.get('bearerToken')
                if token:
                    self.env['ir.config_parameter'].sudo().set_param(
                        self._bearer_token_param_key(), token,
                    )
                return data
            raise UserError(
                "Seerbit Pocket Auth Failed: %s" % res_data.get('message', 'Unknown Error')
            )
        except UserError:
            raise
        except Exception as exc:
            _logger.error("Seerbit Pocket Auth Error: %s", exc)
            if hasattr(exc, 'response') and exc.response is not None:
                _logger.error("Seerbit Pocket Auth API Error Body: %s", exc.response.text)
            raise UserError("Failed to authenticate with Seerbit Pocket: %s" % exc) from exc

    def get_pocket_balance(self):
        if not self.pocket_id:
            raise UserError(
                "Seerbit Pocket ID is not configured for %s." % self.company.name
            )
        url = f'https://pocket.seerbitapi.com/pocket/balance/pocket-id/{self.pocket_id}'
        try:
            headers = {
                'Authorization': f'Bearer {self._get_bearer_token()}',
                'Public-Key': self.public_key,
                'Content-Type': 'application/json',
            }
            response = self._do_request('GET', url, headers=headers)
            response.raise_for_status()
            res_data = response.json()
            _logger.info("Seerbit Pocket Balance Response (%s): %s", self.company.name, res_data)
            if (res_data.get('status') == 'SUCCESS' or str(res_data.get('responseCode')) == '00') and 'data' in res_data:
                data = res_data['data']
                balance = data.get('availableBalanceAmount') or data.get('balance', 0.0)
                return float(balance)
            raise UserError("Seerbit Error: %s" % res_data.get('message', 'Unknown Error'))
        except UserError:
            raise
        except Exception as exc:
            _logger.error("Seerbit Get Pocket Balance Error (%s): %s", self.company.name, exc)
            if hasattr(exc, 'response') and exc.response is not None:
                _logger.error("Seerbit Error Body: %s", exc.response.text)
            return 0.0

    def get_banks(self):
        url = 'https://pocket.seerbitapi.com/pocket/banks/category/NIP'
        try:
            headers = {
                'Authorization': f'Bearer {self._get_bearer_token()}',
                'Public-Key': self.public_key,
                'Content-Type': 'application/json',
            }
            response = self._do_request('GET', url, headers=headers)
            response.raise_for_status()
            res_data = response.json()
            if 'data' in res_data and 'banks' in res_data['data']:
                return res_data['data']['banks']
            if 'content' in res_data:
                return res_data['content']
            return []
        except UserError:
            raise
        except Exception as exc:
            _logger.error("Seerbit Get Banks Error (%s): %s", self.company.name, exc)
            if hasattr(exc, 'response') and exc.response is not None:
                _logger.error("Seerbit Error Body: %s", exc.response.text)
            return []

    def account_enquiry(self, account_number, bank_code):
        url = 'https://pocket.seerbitapi.com/pocket/payout/account-enquiry'
        payload = {'accountnumber': account_number, 'bankcode': bank_code}
        try:
            headers = {
                'Authorization': f'Bearer {self._get_bearer_token()}',
                'Public-Key': self.public_key,
                'Content-Type': 'application/json',
            }
            response = self._do_request('POST', url, json=payload, headers=headers)
            response.raise_for_status()
            res_data = response.json()
            _logger.info("Seerbit Pocket Enquiry Response (%s): %s", self.company.name, res_data)
            if (res_data.get('status') == 'SUCCESS' or str(res_data.get('responseCode')) == '00') and 'data' in res_data:
                return res_data['data']
            return False
        except UserError:
            raise
        except Exception as exc:
            _logger.error("Seerbit Account Enquiry Error (%s): %s", self.company.name, exc)
            return False

    def get_otp(self):
        if not self.pocket_id:
            raise UserError(
                "Seerbit Pocket ID is not configured for %s." % self.company.name
            )
        url = 'https://pocket.seerbitapi.com/pocket/getOtp'
        payload = {'actionItem': 'APPROVE_DISBURSEMENT', 'pocketId': self.pocket_id}
        try:
            headers = {
                'Authorization': f'Bearer {self._get_bearer_token()}',
                'Public-Key': self.public_key,
                'Content-Type': 'application/json',
            }
            response = self._do_request('POST', url, json=payload, headers=headers)
            response.raise_for_status()
            res_data = response.json()
            if res_data.get('status') == 'SUCCESS' or str(res_data.get('responseCode')) == '00':
                return True
            raise UserError(
                "Seerbit OTP Error: %s" % res_data.get('message', 'Failed to generate OTP')
            )
        except UserError:
            raise
        except Exception as exc:
            _logger.error("Seerbit Get OTP Error (%s): %s", self.company.name, exc)
            raise UserError("Failed to generate OTP: %s" % exc) from exc

    def get_signature(self, reference, amount, currency, description, account_number, bank_code, passkey):
        url = 'https://pocket.seerbitapi.com/pocket/payout/get-signature'
        payload = {
            'reference': reference,
            'amount': str(amount),
            'currency': currency,
            'description': description,
            'accountNumber': account_number,
            'bankCode': bank_code,
            'actionType': 'APPROVE_DISBURSEMENT',
            'passKey': passkey,
        }
        headers = {
            'Authorization': f'Bearer {self._get_bearer_token()}',
            'Content-Type': 'application/json',
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
        except Exception as exc:
            error_msg = str(exc)
            if hasattr(exc, 'response') and exc.response is not None:
                try:
                    res_data = exc.response.json()
                    if 'message' in res_data:
                        error_msg = res_data['message']
                except Exception:
                    pass
            raise UserError("Seerbit Signature Error: %s" % error_msg) from exc

    def submit_payout(self, amount, bank_code, account_number, account_name, reference, passkey, currency="NGN", description="Payment"):
        if not self.pocket_id:
            raise UserError(
                "Seerbit Pocket ID is not configured for %s." % self.company.name
            )
        passkey = str(passkey).strip() if passkey else passkey
        amount_str = str(int(amount)) if float(amount).is_integer() else str(amount)
        signature = self.get_signature(
            reference, amount_str, currency, description, account_number, bank_code, passkey,
        )
        url = f'https://pocket.seerbitapi.com/pocket/payout/encrypted/pocket-id/{self.pocket_id}'
        payload = {
            'reference': reference,
            'amount': amount_str,
            'currency': currency,
            'description': description,
            'accountNumber': account_number,
            'bankCode': bank_code,
            'passKey': passkey,
            'actionType': 'APPROVE_DISBURSEMENT',
            'signature': signature,
        }
        try:
            _logger.info("Seerbit Submit Payout Payload (%s): %s", self.company.name, payload)
            headers = {
                'Authorization': f'Bearer {self._get_bearer_token()}',
                'Public-Key': self.public_key,
                'Content-Type': 'application/json',
            }
            response = self._do_request('POST', url, json=payload, headers=headers)
            response.raise_for_status()
            res_data = response.json()
            if res_data.get('status') == 'SUCCESS' or str(res_data.get('responseCode')) == '00':
                return res_data
            raise UserError(
                "Seerbit Payout Error: %s" % res_data.get('message', 'Failed to process payout')
            )
        except UserError:
            raise
        except Exception as exc:
            _logger.error("Seerbit Submit Payout Error (%s): %s", self.company.name, exc)
            error_msg = str(exc)
            if hasattr(exc, 'response') and exc.response is not None:
                _logger.error("Seerbit Error Body: %s", exc.response.text)
                try:
                    res_data = exc.response.json()
                    if 'message' in res_data:
                        error_msg = res_data['message']
                except Exception:
                    pass
            raise UserError("Seerbit Payout Error: %s" % error_msg) from exc

# coding: utf-8
import json
import logging
import pprint
import random
import string
import warnings
import sys
import requests

# Set up logging first
_logger = logging.getLogger(__name__)

FALLBACK_ENDPOINT = "https://posnotification.seerbitapi.com/"

# Suppress all warnings from firebase_admin before importing
warnings.filterwarnings("ignore", category=SyntaxWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

# Suppress specific firebase_admin warnings
if 'firebase_admin' in sys.modules:
    warnings.filterwarnings("ignore", module="firebase_admin")

try:
    import firebase_admin
    from firebase_admin import credentials, firestore
    FIRESTORE_AVAILABLE = True
    
    # Check firebase-admin version for compatibility
    try:
        import pkg_resources
        firebase_version = pkg_resources.get_distribution("firebase-admin").version
        _logger.info("Firebase Admin SDK version: %s", firebase_version)
    except Exception:
        _logger.info("Firebase Admin SDK version: unknown")
        
except ImportError as e:
    FIRESTORE_AVAILABLE = False
    firebase_admin = None
    credentials = None
    firestore = None
    _logger.warning("Firebase Admin SDK not available: %s", str(e))

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from werkzeug.exceptions import Forbidden

from odoo.addons.pos_seerbit.utils import format_erp_ref

# Initialize Firestore only once
_firestore_initialized = False

def initialize_firestore(env):
    """Initialize Firestore with proper error handling"""
    global _firestore_initialized

    # Check if Firestore is available
    if not FIRESTORE_AVAILABLE:
        _logger.warning("Firebase Admin SDK not available. Skipping initialization.")
        return False

    if _firestore_initialized or firebase_admin._apps:
        return True

    try:
        # Get Firestore config from Odoo settings
        config = env['ir.config_parameter'].sudo()
        # Odoo 19: New API with default parameter support
        cred_json = config.get_param('pos_seerbit.seerbit_firestore_cred', default=None)
        project_id = config.get_param('pos_seerbit.seerbit_firestore_project_id', default=None)

        if not cred_json or not project_id:
            _logger.warning("Firestore configuration not available in settings. Skipping initialization.")
            return False

        # Validate JSON format and service account structure
        try:
            cred_dict = json.loads(cred_json)
            # Additional validation - check if it's a valid service account
            if not isinstance(cred_dict, dict) or 'type' not in cred_dict or cred_dict['type'] != 'service_account':
                _logger.error("Invalid service account format in Firestore credentials")
                return False
        except json.JSONDecodeError:
            _logger.error("Invalid JSON format in Firestore service account credentials")
            return False

        # Save credentials to a temporary file
        import tempfile
        import os
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as temp_cred_file:
            temp_cred_file.write(cred_json)
            cred_path = temp_cred_file.name

        try:
            cred = credentials.Certificate(cred_path)
            firebase_admin.initialize_app(cred, {
                'projectId': project_id
            })
            _firestore_initialized = True
            _logger.info("Firestore initialized successfully with project: %s", project_id)
            return True
        finally:
            # Clean up the temporary file
            try:
                os.unlink(cred_path)
            except OSError:
                pass  # File might already be deleted
                
    except Exception as e:
        _logger.warning("Failed to initialize Firestore: %s", str(e))
        return False

# Firestore initialization is deferred until first use
# when a payment method is accessed


# -------------------------------------------------------
#       FALLBACK METHOD FOR API SEND
# -------------------------------------------------------    

def _send_to_fallback(env, payload):
    """
    POST JSON payload to fallback endpoint when Firestore is unavailable.
    """

    try:
        headers = {"Content-Type": "application/json"}
        response = requests.post(FALLBACK_ENDPOINT, json=payload, headers=headers, timeout=30)

        if response.status_code in (200, 201):
            _logger.info("Payload sent to fallback endpoint successfully: %s", payload)
            return True

        else:
            _logger.error(
                "Fallback endpoint returned error %s: %s",
                response.status_code, response.text
            )
            return False

    except Exception as e:
        _logger.error("Failed to send to fallback endpoint: %s", str(e))
        return False


def send_to_firestore_transactions(env, payload):
    """
    Attempts to send the firestore_payload to Firestore if Firebase SDK exists.
    If FIRESTORE_AVAILABLE is False, automatically POST to fallback endpoint.
    """
    firestore_payload = {
        'id': str(payload.get('id', '')),
        'posid': str(payload.get('posid', '')),
        'merchantid': str(payload.get('merchantid', "")),
        'metadata': str(payload.get('metadata', '')),
        'transactionValue': str(payload.get('transactionValue', '')),
        'status': str(payload.get('status', '')),
        'transactionTime': str(payload.get('transactionTime', '')),
        'sessionId': str(payload.get('sessionId', '')),
        'receivedDateTime': str(payload.get('receivedDateTime', '')),
        'transactionRef': str(payload.get('transactionRef', '')),
        'pubkey': str(payload.get('pubkey', '')),
    }

    if not FIRESTORE_AVAILABLE:
        _logger.warning("Firebase Admin SDK not installed. Using fallback endpoint...")
        return _send_to_fallback(env, firestore_payload)

    # Must initialize before firestore.client() or: "The default Firebase app does not exist"
    if not initialize_firestore(env):
        _logger.warning(
            "Firestore not initialized (missing/invalid credentials or settings). Using fallback endpoint..."
        )
        return _send_to_fallback(env, firestore_payload)

    try:
        _logger.info('Sending payment request to Firestore: %s', pprint.pformat(payload))
        db = firestore.client()
        doc_ref = db.collection('transactions').document()
        doc_ref.set(firestore_payload)
        _logger.info(
            'Sent payment request to Firestore successfully. Document ID: %s. Payload: %s',
            doc_ref.id,
            pprint.pformat(firestore_payload),
        )
        return True
    except Exception as e:
        _logger.error("Failed to send payment request to Firestore: %s", str(e))
        _logger.info("Attempting Seerbit fallback endpoint after Firestore error...")
        return _send_to_fallback(env, firestore_payload)


class PosPaymentMethod(models.Model):
    _inherit = "pos.payment.method"

    
    # Seerbit Fields
    seerbit_public_key = fields.Char(
        string="Seerbit Public Key", 
        help="As provided on Seerbit dashboard", 
        copy=False
    )
    seerbit_terminal_id = fields.Char(
        string="Seerbit Terminal ID", 
        help="Terminal ID as provided on Seerbit dashboard", 
        copy=False
    )
    seerbit_latest_response = fields.Char(
        copy=False,
    )  # Server-side buffer for webhook notifications; not loaded to POS.
    
    def _get_payment_terminal_selection(self):
        
        return super()._get_payment_terminal_selection() + [("seerbit", "Seerbit")]


    @api.model
    def _load_pos_data_fields(self, config_id):
       data = super()._load_pos_data_fields(config_id)
       data += ['seerbit_terminal_id', 'seerbit_public_key']
       return data
    @api.constrains('seerbit_terminal_id')
    def _check_seerbit_autoconfirm(self):
        for payment_method in self:
            if not (payment_method.seerbit_public_key and payment_method.seerbit_terminal_id):
                continue
            # Payment methods are now expected to separate at the account levels irrepective of the number of terminals
            
            existing_key = self.search(
                [("id", "!=", payment_method.id), ("seerbit_terminal_id",
                                                   "=", payment_method.seerbit_terminal_id)],
                limit=1,
            )
        
            if existing_key:
                # Restricting duplicate terminals
                raise ValidationError(
                    _("Seerbit terminal %s is already used on payment method %s.")
                    % (payment_method.seerbit_terminal_id, existing_key.display_name)
                )

    def _is_write_forbidden(self, fields):
        whitelisted_fields = {"seerbit_latest_response"}
        return super()._is_write_forbidden(fields - whitelisted_fields)

    @staticmethod
    def _format_erp_ref(ref):
        """
        Format ERP reference to ensure consistent format with 'odoo_' prefix.

        Args:
            ref (str): The ERP reference to format

        Returns:
            str: Formatted reference with 'odoo_' prefix, normalized to lowercase,
                 with whitespace removed, or empty string if ref is None/empty
        """
        return format_erp_ref(ref)

    def send_seerbit_payment_request(self, payload):
        self.ensure_one()
        ok = send_to_firestore_transactions(self.env, payload)
        if ok:
            _logger.info(
                "Seerbit payment request delivered for transaction ID: %s",
                payload.get('id', 'unknown'),
            )
        else:
            _logger.warning(
                "Seerbit payment request could not be delivered (Firestore and fallback failed) for transaction ID: %s",
                payload.get('id', 'unknown'),
            )
        return ok

    def get_latest_seerbit_status(self, expected):
        self.ensure_one()
        stored = self.sudo().seerbit_latest_response
        if stored:
            stored = json.loads(stored)
            # Support both legacy and new payloads
            expected_amount = expected.get(
                "RequestedAmount") or expected.get("transactionValue")
            expected_currency = expected.get(
                "Currency") or stored.get("currency")
            stored_amount = stored.get(
                "transactionValue") or stored.get("RequestedAmount")
            stored_currency = stored.get("currency") or stored.get("Currency")
            
            if (
                expected_currency == stored_currency
                and round(float(expected_amount or 0), 2) == float(stored_amount or 0)
            ):
                self.sudo().seerbit_latest_response = ""  # Avoid reusing responses
                return {
                    "latest_response": stored,
                }
        return False
    @api.model
    def get_firestore_config(self):
        """
        Get Firestore configuration for frontend.
        This method is called by the frontend to get the Firestore config.

        Returns:
            dict: Firestore configuration for frontend
        """
        return self.env['res.config.settings'].sudo().get_firestore_config_for_frontend()

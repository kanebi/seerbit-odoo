# coding: utf-8
import json
import logging
import pprint
import random
import string
import warnings
import sys
import threading

# Set up logging first
_logger = logging.getLogger(__name__)

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
        from importlib.metadata import version as pkg_version
        firebase_version = pkg_version("firebase-admin")
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
from odoo.exceptions import UserError
from werkzeug.exceptions import Forbidden

from odoo.addons.pos_seerbit.utils import format_erp_ref

# Initialize Firestore only once
_firestore_initialized = False

# Persistent HTTP Session Pool for rapid Seerbit requests
_session = None

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
        cred_json = config.get_param('pos_seerbit.seerbit_firestore_cred')
        project_id = config.get_param('pos_seerbit.seerbit_firestore_project_id')

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


# -------------------------------------------------------
#       FALLBACK METHOD FOR API SEND
# -------------------------------------------------------    

def _send_to_fallback(payload):
    from ..services.seerbit_api import SeerbitAPI
    return SeerbitAPI.send_pos_fallback(payload)

def _sync_firestore_or_fallback(firestore_payload):
    """
    Core logic to send to Firestore, or fallback to HTTP.
    Runs inside the thread so it can be monitored with a timeout.
    """
    # ---------- CASE 1: FIRESTORE SDK AVAILABLE ----------
    if FIRESTORE_AVAILABLE and _firestore_initialized:
        try:
            _logger.info('Sending payment request to Firestore: %s', firestore_payload.get('id'))
            
            # Get Firestore client
            db = firestore.client()
            
            # Add to transactions collection
            doc_ref = db.collection('transactions').document()
            doc_ref.set(firestore_payload)
            
            _logger.info('Sent payment request to Firestore successfully. Document ID: %s', doc_ref.id)
            return True
        except Exception as e:
            _logger.error("Failed to send payment request to Firestore: %s", str(e))
            _logger.warning("Falling back to endpoint...")
            return _send_to_fallback(firestore_payload)
    # ---------- CASE 2: FIRESTORE SDK NOT AVAILABLE ----------
    else:
        _logger.warning("Firestore SDK not initialized. Using fallback endpoint...")
        return _send_to_fallback(firestore_payload)

def send_to_firestore_transactions(env, payload):
    """
    Attempts to send the firestore_payload to Firestore if Firebase SDK exists.
    If FIRESTORE_AVAILABLE is False, automatically POST to fallback endpoint.
    
    Waits synchronously for up to 5 seconds. If the process exceeds 5 seconds,
    it detaches and lets the transmission finish in the background.
    """
    # Initialize firestore dynamically (synchronous, uses Odoo env cursor safely)
    initialize_firestore(env)
    
    # Ensure all values are stringified and add server timestamp
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
    
    # Spawn thread to handle network calls
    thread = threading.Thread(
        target=_sync_firestore_or_fallback,
        args=(firestore_payload,),
        daemon=True
    )
    thread.start()
    
    # Wait synchronously for up to 5 seconds
    thread.join(timeout=5.0)
    
    if thread.is_alive():
        _logger.warning(
            "Payment request transmission exceeded 5 seconds. "
            "Detaching and continuing in the background for transaction ID: %s", 
            firestore_payload.get('id')
        )
        # Thread continues running in background safely
        return True
        
    return True

class PosPaymentMethod(models.Model):
    _inherit = "pos.payment.method"

    def _get_payment_method_type(self):
        selection = super()._get_payment_method_type()
        if not any(k == 'transfer' for k, _v in selection):
            selection.append(('transfer', _("Transfer")))
        return selection

    seerbit_transfer_type = fields.Selection([
        ('static', 'Static'),
        ('dynamic', 'Dynamic')
    ], string="Transfer Type", default='static')

    seerbit_va_id = fields.Many2one(
        'seerbit.virtual.account',
        string="Static Virtual Account",
        domain="[('company_id', '=', company_id), ('va_type', '=', 'pos_static')]",
        ondelete='restrict'
    )

    seerbit_va_account_number = fields.Char(
        related='seerbit_va_id.account_number',
        string="Account Number",
        readonly=True
    )
    seerbit_va_bank_name = fields.Char(
        related='seerbit_va_id.bank_name',
        string="Bank Name",
        readonly=True
    )
    seerbit_va_name = fields.Char(
        related='seerbit_va_id.name',
        string="Account Name",
        readonly=True
    )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for record in records:
            if record.payment_method_type == 'transfer' and record.seerbit_transfer_type == 'static' and not record.seerbit_va_id:
                try:
                    record._generate_static_va()
                except Exception as e:
                    _logger.warning("Could not auto-generate Static Virtual Account on create for %s: %s", record.name, e)
            elif record.seerbit_transfer_type == 'dynamic' and record.seerbit_va_id:
                record.seerbit_va_id = False
        return records

    def write(self, vals):
        res = super().write(vals)
        for record in self:
            if record.payment_method_type == 'transfer' and record.seerbit_transfer_type == 'dynamic' and record.seerbit_va_id:
                record.seerbit_va_id = False
            elif record.payment_method_type == 'transfer' and record.seerbit_transfer_type == 'static' and not record.seerbit_va_id:
                # If static VA is not set, generate a new dedicated VA on save
                try:
                    record._generate_static_va()
                except Exception as e:
                    _logger.warning("Could not auto-generate Static Virtual Account on write for %s: %s", record.name, e)
        return res

    @api.onchange('payment_method_type', 'seerbit_transfer_type')
    def _onchange_seerbit_payment_method_type(self):
        if self.payment_method_type != 'transfer':
            self.seerbit_va_id = False
            self.seerbit_transfer_type = 'static'
        elif self.seerbit_transfer_type == 'dynamic':
            self.seerbit_va_id = False

    def _generate_static_va(self, force=False):
        """
        Creates and links a new Static Virtual Account via SeerBit API.
        Used on save (create/write) when seerbit_va_id is empty, or explicitly via button action.
        Dynamic transfer methods NEVER generate a VA on save/creation.
        """
        self.ensure_one()
        if self.payment_method_type != 'transfer' or self.seerbit_transfer_type != 'static':
            return False
        if not force and self.seerbit_va_id and self.seerbit_va_id.account_number:
            return self.seerbit_va_id

        company = self.company_id or self.env.company
        if not company.seerbit_public_key:
            _logger.info("Company %s has no SeerBit public key configured. Skipping VA generation.", company.name)
            return False

        partner = company.partner_id
        email = partner.email or 'pos@seerbit.com'
        email_user, email_domain = email.split('@') if '@' in email else (email, 'seerbit.com')

        from ..services.seerbit_api import SeerbitAPI
        api_client = SeerbitAPI(self.env, company=company)

        import uuid
        tag = uuid.uuid4().hex[:6]
        va_ref = f"POS_STATIC_{self.id or tag}_{uuid.uuid4().hex[:4]}"
        va_name = f"{company.name} - {self.name}"
        # Use unique email per static account so SeerBit reserves a dedicated customer account
        unique_email = f"{email_user}+pos_{self.id or tag}@{email_domain}"

        try:
            with self.env.cr.savepoint():
                res = api_client.create_virtual_account(
                    full_name=va_name,
                    reference=va_ref,
                    email=unique_email,
                )
                payments_data = res.get('payments') if isinstance(res.get('payments'), dict) else res
                account_number = payments_data.get('accountNumber') if isinstance(payments_data, dict) else False
                bank_name = payments_data.get('bankName', 'SeerBit Bank') if isinstance(payments_data, dict) else 'SeerBit Bank'

                if not account_number:
                    err_msg = res.get('message') or (payments_data.get('message') if isinstance(payments_data, dict) else '')
                    raise UserError(_("SeerBit did not return an account number: %s") % (err_msg or _("Please check company credentials.")))

                # Create or fetch bank
                bank = self.env['res.bank'].search([('name', '=', bank_name)], limit=1)
                if not bank:
                    bank = self.env['res.bank'].create({'name': bank_name})

                # Register in partner bank
                self.env['res.partner.bank'].create({
                    'acc_number': account_number,
                    'partner_id': partner.id,
                    'bank_id': bank.id,
                })

                # Create VA model record
                va_record = self.env['seerbit.virtual.account'].create({
                    'name': payments_data.get('walletName', va_name),
                    'company_id': company.id,
                    'partner_id': partner.id,
                    'reference': payments_data.get('reference', va_ref),
                    'account_number': account_number,
                    'bank_name': bank_name,
                    'va_type': 'pos_static',
                })

                self.seerbit_va_id = va_record.id
                return va_record
        except UserError:
            raise
        except Exception as e:
            _logger.error("Failed to generate Static Virtual Account for %s: %s", self.name, e)
            raise UserError(_("Failed to generate Virtual Account from SeerBit: %s") % str(e))

    def action_generate_static_va(self):
        """
        Creates a new Static Virtual Account via SeerBit API and links it.
        Uses the merchant's configured company credentials.
        """
        self.ensure_one()
        if self.payment_method_type != 'transfer':
            raise UserError(_("Payment method must be configured as Integration: Transfer."))
        if self.seerbit_transfer_type != 'static':
            raise UserError(_("Virtual Account generation is only applicable for Static Transfer Type. Dynamic Virtual Accounts are generated automatically per order during POS checkout."))

        company = self.company_id or self.env.company
        partner = company.partner_id
        if not partner.email:
            raise UserError(_("The company '%s' must have an email address to create a Virtual Account.") % company.name)

        va = self._generate_static_va(force=True)
        if not va:
            raise UserError(_("Failed to generate Virtual Account. Please verify your company SeerBit configuration."))

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Success"),
                'message': _("Static Virtual Account successfully generated (%s: %s)") % (
                    va.bank_name, va.account_number
                ),
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'reload'},
            }
        }

    def create_dynamic_va_for_pos(self, order_ref, amount, customer_name=None, customer_email=None):
        """
        Called during POS checkout when using a Dynamic Transfer payment method.
        Generates a unique dynamic virtual account from SeerBit API tied to the order and amount.
        """
        self.ensure_one()
        if self.payment_method_type != 'transfer' or self.seerbit_transfer_type != 'dynamic':
            raise UserError(_("Dynamic VA can only be generated for Dynamic Transfer payment methods."))
        if not amount or float(amount) <= 1.0:
            raise UserError(_("Transfer payment amount must be greater than 1.00."))

        company = self.company_id or self.env.company
        partner = company.partner_id
        email = customer_email or partner.email or 'pos@seerbit.com'
        name = customer_name or f"Order {order_ref}"

        from ..services.seerbit_api import SeerbitAPI
        api_client = SeerbitAPI(self.env, company=company)

        import uuid
        reference = f"POS_DYN_{order_ref}_{uuid.uuid4().hex[:4]}"
        res = api_client.create_dynamic_virtual_account(
            reference=reference,
            amount=amount,
            customer_name=name,
            customer_email=email,
            expiry_minutes=30,
        )

        payments_data = res.get('payments') if isinstance(res.get('payments'), dict) else res
        account_number = payments_data.get('accountNumber') or res.get('accountNumber')
        bank_name = payments_data.get('bankName') or res.get('bankName', 'SeerBit Bank')
        wallet_name = payments_data.get('walletName') or res.get('walletName', name)
        assigned_ref = payments_data.get('reference') or res.get('reference', reference)

        bank = self.env['res.bank'].search([('name', '=', bank_name)], limit=1)
        if not bank:
            bank = self.env['res.bank'].create({'name': bank_name})

        va_record = self.env['seerbit.virtual.account'].create({
            'name': wallet_name,
            'company_id': company.id,
            'partner_id': partner.id,
            'reference': assigned_ref,
            'account_number': account_number,
            'bank_name': bank_name,
            'va_type': 'pos_dynamic',
        })

        return {
            'va_id': va_record.id,
            'account_number': account_number,
            'bank_name': bank_name,
            'account_name': wallet_name,
            'reference': assigned_ref,
            'amount': amount,
            'expiry_minutes': 30,
        }
    
    # Branch public key comes from company Seerbit settings (not stored per payment method).
    seerbit_public_key = fields.Char(
        string="Seerbit Public Key",
        compute='_compute_seerbit_public_key',
        help="Resolved from this payment method's company Seerbit settings.",
    )
    seerbit_terminal_id = fields.Char(
        string="Seerbit Terminal ID", 
        help="Terminal ID as provided on Seerbit dashboard", 
        copy=False
    )
    # Transient buffer for async Seerbit notifications. No field groups — POS session
    # load reads this as the terminal user; ERP-manager groups caused ACL denials.
    seerbit_latest_response = fields.Char(copy=False)

    @api.depends('company_id', 'company_id.seerbit_public_key')
    def _compute_seerbit_public_key(self):
        for pm in self:
            pm.seerbit_public_key = pm.company_id.seerbit_public_key or ''

    def _get_payment_terminal_selection(self):
        
        return super()._get_payment_terminal_selection() + [("seerbit", "Seerbit")]


    @api.model
    def _load_pos_data_fields(self, config):
        data = super()._load_pos_data_fields(config)
        data += [
            'seerbit_terminal_id',
            'seerbit_public_key',
            'seerbit_latest_response',
            'seerbit_transfer_type',
            'seerbit_va_id',
            'seerbit_va_account_number',
            'seerbit_va_bank_name',
            'seerbit_va_name',
        ]
        return data

    @api.constrains("seerbit_terminal_id")
    def _check_seerbit_terminal_share(self):
        """Same terminal ID may be shared across payment methods (e.g. Small Chops 1 & 2)."""
        return

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
        
        
        # Try to send to Firestore
        firestore_success = send_to_firestore_transactions(self.env, payload)
        
        if firestore_success:
            _logger.info(
                "Seerbit payment request processed successfully for transaction ID: %s", payload.get('id', 'unknown'))
        else:
            _logger.warning(
                "Seerbit payment request saved to Odoo but Firestore send failed for transaction ID: %s", payload.get('id', 'unknown'))
        
        return False

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

# -*- coding: utf-8 -*-
"""
ResConfigSettings Extension for Seerbit Module

This module extends Odoo's configuration settings to handle Seerbit payment terminal
configuration. It provides a checkbox in Settings to enable/disable the Seerbit module
and automatically manages payment method configurations when the module is disabled.

Key Features:
- Module enable/disable toggle
- Conditional Firestore configuration fields (only shown when Seerbit is enabled)
- Automatic cleanup of Seerbit payment methods when module is disabled
- Proper access rights handling
- Comprehensive error handling and logging
"""

import logging
import json
from odoo import fields, models, api
from odoo.exceptions import AccessError, ValidationError

# Set up logging for this module
_logger = logging.getLogger(__name__)


class ResConfigSettings(models.TransientModel):
    """
    Configuration Settings for Seerbit Module
    
    This class extends Odoo's base configuration settings to add Seerbit-specific
    configuration options. It inherits from res.config.settings which is Odoo's
    standard way to handle system configuration.
    """
    _inherit = "res.config.settings"

    # Configuration field for enabling/disabling Seerbit module
    module_pos_seerbit = fields.Boolean(
        string="Seerbit Payment Terminal",
        help="""Enable Seerbit payment terminal integration.
                When enabled, transactions will be processed and synced with your Seerbit POS Terminal.
                Set your terminal credentials on the payment method configuration.""",
    )
    
    seerbit_auto_post = fields.Boolean(
        string="Automatically Post Payments",
        help="Automatically post Seerbit payments to the ledger when received.",
    )
    
    seerbit_auto_reconcile = fields.Boolean(
        string="Automatic Reconciliation",
        help="Automatically reconcile Seerbit payments with invoices when payment is received.",
    )
    
    # Firestore Configuration Fields (only shown when Seerbit is enabled)
    # Note: These fields are not stored in the database but managed through ir.config_parameter
    seerbit_firestore_cred = fields.Char(
        string="Firestore Service Account JSON",
        help="Paste the content of your Firestore service account JSON file here.",
        groups="base.group_erp_manager",
    )
    seerbit_firestore_project_id = fields.Char(
        string="Firestore Project ID",
        help="The Project ID of your Firestore project.",
        groups="base.group_erp_manager",
        default="pospushnotif",
    )
    seerbit_firebase_api_key = fields.Char(
        string="Firebase API Key",
        help="The API key for your Firebase project (same for Firestore).",
        groups="base.group_erp_manager",
    )
    seerbit_public_key = fields.Char(
        string="Seerbit Public Key",
        help="Global Seerbit Public Key, used as default for terminals and for global operations.",
        groups="base.group_erp_manager",
    )
    seerbit_secret_key = fields.Char(
        string="Seerbit Secret Key",
        help="As provided on Seerbit dashboard. Used for Invoicing and Virtual Accounts.",
        groups="base.group_erp_manager",
    )
    seerbit_pocket_id = fields.Char(
        string="Seerbit Pocket ID",
        help="Your Seerbit Pocket ID for fetching balances and processing payouts.",
        groups="base.group_erp_manager",
    )
    seerbit_pocket_email = fields.Char(
        string="Pocket Email",
        help="Merchant email for Seerbit Pocket API.",
        groups="base.group_erp_manager",
    )
    seerbit_pocket_password = fields.Char(
        string="Pocket Password",
        help="Merchant password for Seerbit Pocket API.",
        groups="base.group_erp_manager",
    )

    @api.constrains('seerbit_firestore_cred')
    def _validate_firestore_cred(self):
        """Validate Firestore service account JSON"""
        for record in self:
            if record.seerbit_firestore_cred and record.module_pos_seerbit:
                try:
                    json.loads(record.seerbit_firestore_cred)
                except json.JSONDecodeError:
                    raise ValidationError("Invalid JSON format in Firestore Service Account JSON")

    def set_values(self):
        """Save configuration values to system parameters"""
        super().set_values()
        
        # Save Firestore configuration to ir.config_parameter
        self.env['ir.config_parameter'].sudo().set_param('pos_seerbit.seerbit_firestore_cred', self.seerbit_firestore_cred or '')
        self.env['ir.config_parameter'].sudo().set_param('pos_seerbit.seerbit_firestore_project_id', self.seerbit_firestore_project_id or '')
        self.env['ir.config_parameter'].sudo().set_param('pos_seerbit.seerbit_firebase_api_key', self.seerbit_firebase_api_key or '')
        self.env['ir.config_parameter'].sudo().set_param('pos_seerbit.seerbit_public_key', self.seerbit_public_key or '')
        self.env['ir.config_parameter'].sudo().set_param('pos_seerbit.seerbit_secret_key', self.seerbit_secret_key or '')
        self.env['ir.config_parameter'].sudo().set_param('pos_seerbit.seerbit_pocket_id', self.seerbit_pocket_id or '')
        self.env['ir.config_parameter'].sudo().set_param('pos_seerbit.seerbit_pocket_email', self.seerbit_pocket_email or '')
        self.env['ir.config_parameter'].sudo().set_param('pos_seerbit.seerbit_pocket_password', self.seerbit_pocket_password or '')
        self.env['ir.config_parameter'].sudo().set_param('pos_seerbit.seerbit_auto_post', str(self.seerbit_auto_post))
        self.env['ir.config_parameter'].sudo().set_param('pos_seerbit.seerbit_auto_reconcile', str(self.seerbit_auto_reconcile))
        
        # Log configuration changes
        if self.module_pos_seerbit:
            _logger.info("Seerbit module enabled with Firestore configuration")
        else:
            _logger.info("Seerbit module disabled")

    def get_values(self):
        """Load configuration values from system parameters"""
        res = super().get_values()
        res.update(
            seerbit_firestore_cred=self.env['ir.config_parameter'].sudo().get_param('pos_seerbit.seerbit_firestore_cred', default=''),
            seerbit_firestore_project_id=self.env['ir.config_parameter'].sudo().get_param('pos_seerbit.seerbit_firestore_project_id', default=''),
            seerbit_firebase_api_key=self.env['ir.config_parameter'].sudo().get_param('pos_seerbit.seerbit_firebase_api_key', default=''),
            seerbit_public_key=self.env['ir.config_parameter'].sudo().get_param('pos_seerbit.seerbit_public_key', default=''),
            seerbit_secret_key=self.env['ir.config_parameter'].sudo().get_param('pos_seerbit.seerbit_secret_key', default=''),
            seerbit_pocket_id=self.env['ir.config_parameter'].sudo().get_param('pos_seerbit.seerbit_pocket_id', default=''),
            seerbit_pocket_email=self.env['ir.config_parameter'].sudo().get_param('pos_seerbit.seerbit_pocket_email', default=''),
            seerbit_pocket_password=self.env['ir.config_parameter'].sudo().get_param('pos_seerbit.seerbit_pocket_password', default=''),
            seerbit_auto_post=self.env['ir.config_parameter'].sudo().get_param('pos_seerbit.seerbit_auto_post', default='True') == 'True',
            seerbit_auto_reconcile=self.env['ir.config_parameter'].sudo().get_param('pos_seerbit.seerbit_auto_reconcile', default='True') == 'True',
        )
        return res

    @api.model
    def get_firestore_config_for_frontend(self):
        """
        Get Firestore configuration for frontend use.
        This method is called by the frontend to get the Firestore config.
        
        Returns:
            dict: Firestore configuration for frontend
        """
        config = self.env['ir.config_parameter'].sudo()
        return {
            'apiKey': config.get_param('pos_seerbit.seerbit_firebase_api_key', default=''),
            'projectId': config.get_param('pos_seerbit.seerbit_firestore_project_id', default=''),
        }

    @api.model
    def get_firestore_config_for_backend(self):
        """
        Get Firestore configuration for backend use.
        This method is called by the backend to get the Firestore config.
        
        Returns:
            dict: Firestore configuration for backend
        """
        config = self.env['ir.config_parameter'].sudo()
        return {
            'credJson': config.get_param('pos_seerbit.seerbit_firestore_cred', default=''),
            'projectId': config.get_param('pos_seerbit.seerbit_firestore_project_id', default=''),
        }

    @api.model
    def validate_firestore_config(self):
        """
        Validate Firestore configuration.
        
        Returns:
            dict: Validation result with status and message
        """
        try:
            config = self.get_firestore_config_for_backend()
            
            if not config['credJson']:
                return {'status': 'error', 'message': 'Firestore Service Account JSON is required'}
            
            if not config['projectId']:
                return {'status': 'error', 'message': 'Firestore Project ID is required'}
            
            # Validate JSON format
            try:
                json.loads(config['credJson'])
            except json.JSONDecodeError:
                return {'status': 'error', 'message': 'Invalid JSON format in Firestore Service Account JSON'}
            
            return {'status': 'success', 'message': 'Firestore configuration is valid'}
            
        except Exception as e:
            _logger.error("Error validating Firestore configuration: %s", str(e))
            return {'status': 'error', 'message': f'Validation error: {str(e)}'}

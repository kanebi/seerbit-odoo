# -*- coding: utf-8 -*-
"""
ResConfigSettings Extension for Seerbit Module
"""

import json
import logging

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    module_pos_seerbit = fields.Boolean(
        string="Seerbit Payment Terminal",
        help="Enable Seerbit payment terminal integration.",
    )

    # Global (shared) Firestore — not per company
    seerbit_firestore_cred = fields.Char(
        string="Firestore Service Account JSON",
        groups="base.group_erp_manager",
    )
    seerbit_firestore_project_id = fields.Char(
        string="Firestore Project ID",
        groups="base.group_erp_manager",
        default="pospushnotif",
    )
    seerbit_firebase_api_key = fields.Char(
        string="Firebase API Key",
        groups="base.group_erp_manager",
    )

    # Per-company business configuration (stored on res.company)
    seerbit_business_config_title = fields.Char(compute='_compute_seerbit_business_config_title')
    seerbit_public_key = fields.Char(
        related='company_id.seerbit_public_key',
        readonly=False,
        groups="base.group_erp_manager",
    )
    seerbit_secret_key = fields.Char(
        related='company_id.seerbit_secret_key',
        readonly=False,
        groups="base.group_erp_manager",
    )
    seerbit_pocket_id = fields.Char(
        related='company_id.seerbit_pocket_id',
        readonly=False,
        groups="base.group_erp_manager",
    )
    seerbit_pocket_email = fields.Char(
        related='company_id.seerbit_pocket_email',
        readonly=False,
        groups="base.group_erp_manager",
    )
    seerbit_pocket_password = fields.Char(
        related='company_id.seerbit_pocket_password',
        readonly=False,
        groups="base.group_erp_manager",
    )
    seerbit_auto_post = fields.Boolean(
        related='company_id.seerbit_auto_post',
        readonly=False,
    )
    seerbit_auto_reconcile = fields.Boolean(
        related='company_id.seerbit_auto_reconcile',
        readonly=False,
    )

    @api.depends('company_id', 'company_id.name')
    def _compute_seerbit_business_config_title(self):
        for settings in self:
            company_name = settings.company_id.name or _('Company')
            settings.seerbit_business_config_title = _(
                'Seerbit Business Configuration for %s', company_name,
            )

    @api.constrains('seerbit_firestore_cred')
    def _validate_firestore_cred(self):
        for record in self:
            if record.seerbit_firestore_cred and record.module_pos_seerbit:
                try:
                    json.loads(record.seerbit_firestore_cred)
                except json.JSONDecodeError as exc:
                    raise ValidationError(
                        _("Invalid JSON format in Firestore Service Account JSON")
                    ) from exc

    def set_values(self):
        super().set_values()
        icp = self.env['ir.config_parameter'].sudo()
        icp.set_param('pos_seerbit.seerbit_firestore_cred', self.seerbit_firestore_cred or '')
        icp.set_param('pos_seerbit.seerbit_firestore_project_id', self.seerbit_firestore_project_id or '')
        icp.set_param('pos_seerbit.seerbit_firebase_api_key', self.seerbit_firebase_api_key or '')

    def get_values(self):
        res = super().get_values()
        icp = self.env['ir.config_parameter'].sudo()
        res.update(
            seerbit_firestore_cred=icp.get_param('pos_seerbit.seerbit_firestore_cred', default=''),
            seerbit_firestore_project_id=icp.get_param('pos_seerbit.seerbit_firestore_project_id', default=''),
            seerbit_firebase_api_key=icp.get_param('pos_seerbit.seerbit_firebase_api_key', default=''),
        )
        return res

    @api.model
    def get_firestore_config_for_frontend(self):
        icp = self.env['ir.config_parameter'].sudo()
        return {
            'apiKey': icp.get_param('pos_seerbit.seerbit_firebase_api_key', default=''),
            'projectId': icp.get_param('pos_seerbit.seerbit_firestore_project_id', default=''),
        }

    @api.model
    def get_firestore_config_for_backend(self):
        icp = self.env['ir.config_parameter'].sudo()
        return {
            'credJson': icp.get_param('pos_seerbit.seerbit_firestore_cred', default=''),
            'projectId': icp.get_param('pos_seerbit.seerbit_firestore_project_id', default=''),
        }

    @api.model
    def validate_firestore_config(self):
        try:
            config = self.get_firestore_config_for_backend()
            if not config['credJson']:
                return {'status': 'error', 'message': 'Firestore Service Account JSON is required'}
            if not config['projectId']:
                return {'status': 'error', 'message': 'Firestore Project ID is required'}
            json.loads(config['credJson'])
            return {'status': 'success', 'message': 'Firestore configuration is valid'}
        except json.JSONDecodeError:
            return {'status': 'error', 'message': 'Invalid JSON format in Firestore Service Account JSON'}
        except Exception as exc:
            _logger.error("Error validating Firestore configuration: %s", exc)
            return {'status': 'error', 'message': f'Validation error: {exc}'}

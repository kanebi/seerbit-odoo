# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ResCompany(models.Model):
    _inherit = 'res.company'

    seerbit_public_key = fields.Char(
        string='Seerbit Public Key',
        help='Seerbit public key for this branch (POS terminals and invoicing). '
             'Must be unique across companies when set — webhooks route by this key.',
    )
    seerbit_secret_key = fields.Char(
        string='Seerbit Secret Key',
        help='Seerbit secret key for this branch (invoicing, virtual accounts, API).',
        groups='base.group_erp_manager',
    )
    seerbit_pocket_id = fields.Char(
        string='Seerbit Pocket ID',
        help='Seerbit Pocket ID for balances and payouts for this branch.',
        groups='base.group_erp_manager',
    )
    seerbit_pocket_email = fields.Char(
        string='Seerbit Pocket Email',
        help='Merchant email for Seerbit Pocket API for this branch.',
        groups='base.group_erp_manager',
    )
    seerbit_pocket_password = fields.Char(
        string='Seerbit Pocket Password',
        help='Merchant password for Seerbit Pocket API for this branch.',
        groups='base.group_erp_manager',
    )
    seerbit_auto_post = fields.Boolean(
        string='Automatically Post Seerbit Payments',
        default=True,
        help='Post Seerbit payments to the ledger when received for this branch.',
    )
    seerbit_auto_reconcile = fields.Boolean(
        string='Automatic Seerbit Reconciliation',
        default=True,
        help='Reconcile Seerbit payments with invoices when received for this branch.',
    )

    @api.constrains('seerbit_public_key')
    def _check_seerbit_public_key_unique(self):
        for company in self:
            key = (company.seerbit_public_key or '').strip()
            if not key:
                continue
            duplicates = self.sudo().search([
                ('id', '!=', company.id),
                ('seerbit_public_key', '=', key),
            ], limit=1)
            if duplicates:
                raise ValidationError(_(
                    "Seerbit Public Key must be unique per company. "
                    "%(key)s is already used by %(company)s. "
                    "Webhooks cannot route payments correctly when keys are shared.",
                    key=key,
                    company=duplicates.name,
                ))

    @api.model
    def seerbit_company(self, company=None):
        """Sudo company record used for per-branch Seerbit business configuration."""
        if company:
            if isinstance(company, int):
                return self.browse(company).sudo()
            company.ensure_one()
            return company.sudo()
        return self.env.company.sudo()

    @api.model
    def seerbit_company_by_public_key(self, public_key):
        """Resolve company from Seerbit public key. Empty recordset if unknown/ambiguous."""
        key = (public_key or '').strip()
        if not key:
            return self.browse()
        matches = self.sudo().search([('seerbit_public_key', '=', key)])
        if len(matches) > 1:
            # Should be blocked by constraint; still guard webhook/POS against legacy data.
            return self.browse()
        return matches

    def seerbit_pocket_bearer_param_key(self):
        self.ensure_one()
        return f'pos_seerbit.pocket_bearer_token.{self.id}'

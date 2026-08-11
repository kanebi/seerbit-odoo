from odoo import api, fields, models, _
from odoo.exceptions import UserError
import logging
import uuid

_logger = logging.getLogger(__name__)

class ResPartner(models.Model):
    _inherit = 'res.partner'

    seerbit_va_ids = fields.One2many(
        'seerbit.virtual.account', 'partner_id', string='Virtual Accounts'
    )
    seerbit_va_balance = fields.Monetary(
        string='Total VA Balance', 
        compute='_compute_seerbit_va_balance',
        help="Outstanding credits across all Virtual Accounts for this customer."
    )

    def _compute_seerbit_va_balance(self):
        for partner in self:
            partner.seerbit_va_balance = sum(partner.seerbit_va_ids.mapped('balance'))

    @api.model_create_multi
    def create(self, vals_list):
        partners = super(ResPartner, self).create(vals_list)
        for partner in partners:
            if not partner.parent_id and partner.email:
                # Attempt to auto-create VA if email is provided and it's a top-level contact (individual or company)
                try:
                    partner.action_create_seerbit_va()
                except Exception as e:
                    _logger.warning(f"Could not auto-create Seerbit VA for {partner.name}: {e}")
        return partners

    def action_create_seerbit_va(self):
        self.ensure_one()
        if not self.email:
            raise UserError(_("Customer must have an email address to create a Virtual Account."))
            
        SeerbitAPI = self.env['pos_seerbit.services']._get_api() if hasattr(self.env, 'pos_seerbit.services') else None
        if not SeerbitAPI:
            from ..services.seerbit_api import SeerbitAPI as APIClass
            SeerbitAPI = APIClass(self.env)
            
        reference = f"VA_{self.id}_{uuid.uuid4().hex[:8]}"
        
        try:
            res = SeerbitAPI.create_virtual_account(
                full_name=self.name,
                reference=reference,
                email=self.email
            )
            
            payments_data = res.get('payments', {})
            
            # Create a bank account record in Odoo
            bank = self.env['res.bank'].search([('name', '=', payments_data.get('bankName'))], limit=1)
            if not bank:
                bank = self.env['res.bank'].create({'name': payments_data.get('bankName')})
                
            self.env['res.partner.bank'].create({
                'acc_number': payments_data.get('accountNumber'),
                'partner_id': self.id,
                'bank_id': bank.id,
            })
            
            # Create the virtual account record
            self.env['seerbit.virtual.account'].create({
                'partner_id': self.id,
                'reference': payments_data.get('reference', reference),
                'account_number': payments_data.get('accountNumber'),
                'bank_name': payments_data.get('bankName'),
                'name': payments_data.get('walletName'),
            })
            
        except Exception as e:
            raise UserError(_("Error creating Virtual Account: %s" % str(e)))

    def action_view_virtual_accounts(self):
        self.ensure_one()
        return {
            'name': _('Customer VA Payments'),
            'type': 'ir.actions.act_window',
            'res_model': 'account.payment',
            'view_mode': 'list,form',
            'domain': [('partner_id', '=', self.id), ('seerbit_va_id', '!=', False)],
            'context': {
                'default_partner_id': self.id,
                'search_default_groupby_seerbit_va_id': 1,
            }
        }

    def _reconcile_seerbit_payment(self, payment):
        if payment.state != 'in_process':
            return
            
        liquidity_lines, _, _ = payment._seek_for_lines()
        if not liquidity_lines:
            _logger.warning(f"Seerbit Recon: No liquidity lines found for payment {payment.name}")
            _logger.warning(f"Seerbit Recon Debug {payment.name}: Payment State is {payment.state}")
            _logger.warning(f"Seerbit Recon Debug {payment.name}: Move State is {payment.move_id.state}")
            for l in payment.move_id.line_ids:
                _logger.warning(f"Seerbit Recon Debug {payment.name}: Line {l.id} has account {l.account_id.code} - {l.account_id.name} (Type: {l.account_id.account_type}). Debit: {l.debit}, Credit: {l.credit}")
            
            valid_accs = [a.name for a in payment._get_valid_liquidity_accounts()]
            _logger.warning(f"Seerbit Recon Debug {payment.name}: Valid Liquidity Accounts expected by Odoo: {valid_accs}")
            return
            
        st_line = self.env['account.bank.statement.line'].create({
            'payment_ref': payment.memo or 'Seerbit Payment',
            'journal_id': payment.journal_id.id,
            'amount': payment.amount if payment.payment_type == 'inbound' else -payment.amount,
            'date': payment.date,
            'partner_id': payment.partner_id.id,
        })
        
        if st_line.move_id.state == 'draft':
            st_line.move_id.action_post()
            
        suspense_line = st_line.move_id.line_ids.filtered(lambda l: l.account_id == st_line.journal_id.suspense_account_id)
        if not suspense_line:
            # Fallback if no suspense account explicitly set
            suspense_line = st_line.move_id.line_ids.filtered(lambda l: l.id != st_line.id and l.account_id != st_line.journal_id.default_account_id)[:1]

        if suspense_line and liquidity_lines:
            try:
                suspense_line.account_id = liquidity_lines.account_id.id
                (suspense_line | liquidity_lines).reconcile()
                _logger.info(f"Successfully bank-reconciled Seerbit payment {payment.name}")
            except Exception as e:
                _logger.error(f"Failed to bank-reconcile Seerbit payment {payment.name}: {e}")
        else:
            _logger.warning(f"Seerbit Recon: Could not find suspense line for payment {payment.name}")

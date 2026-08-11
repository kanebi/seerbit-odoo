from odoo import api, fields, models, _
from odoo.exceptions import UserError
import uuid
import logging
from ..services.seerbit_pocket_api import SeerbitPocketAPI, require_seerbit_auth

_logger = logging.getLogger(__name__)

class SeerbitPayout(models.Model):
    _name = 'seerbit.payout'
    _description = 'Seerbit Payout'
    _order = 'create_date desc'

    name = fields.Char(string='Reference', required=True, copy=False, readonly=True, default=lambda self: _('New'))
    amount = fields.Monetary(string='Amount', required=True, currency_field='currency_id')
    currency_id = fields.Many2one('res.currency', default=lambda self: self.env.company.currency_id)
    description = fields.Text(string='Description', default=lambda self: f"Payment from {self.env.company.name}")
    
    partner_id = fields.Many2one('res.partner', string='Vendor', domain=[('supplier_rank', '>', 0)])
    move_id = fields.Many2one(
        'account.move', 
        string='Vendor Bill', 
        domain="[('move_type', '=', 'in_invoice'), ('state', '=', 'posted'), ('payment_state', 'in', ('not_paid', 'partial')), '|', ('partner_id', '=', partner_id), ('partner_id', '=', False)]"
    )
    partner_bank_id = fields.Many2one('res.partner.bank', string="Recipient Bank Account", domain="[('partner_id', '=', partner_id)]")
    is_trusted_bank = fields.Boolean(related='partner_bank_id.allow_out_payment', string="Is Trusted Bank")
    
    bank_code = fields.Char(string='Bank Code')
    bank_name = fields.Char(string='Bank Name', required=True) 
    
    account_number = fields.Char(string='Account Number', required=True)
    account_name = fields.Char(string='Account Name', required=True)
    
    is_account_verified = fields.Boolean(string='Account Verified', readonly=True, copy=False)
    verified_account_name = fields.Char(string='Verified Account Name', readonly=True, copy=False)
    
    state = fields.Selection([
        ('draft', 'Draft'),
        ('pending_otp', 'Pending OTP'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ], string='Status', default='draft', readonly=True, copy=False)
    
    otp_code = fields.Char(string='OTP', copy=False)
    last_otp_sent_time = fields.Datetime(string='Last OTP Sent', copy=False)
    payment_id = fields.Many2one('account.payment', string='Odoo Payment', readonly=True, copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = f"PAYOUT-{uuid.uuid4().hex[:6].upper()}"
        return super().create(vals_list)

    @api.onchange('partner_id')
    def _onchange_partner_id(self):
        if self.partner_id:
            # Clear bill if it doesn't belong to the new partner
            if self.move_id and self.move_id.partner_id != self.partner_id:
                self.move_id = False
            
            # Auto-populate bank if not set or belongs to another partner
            if not self.partner_bank_id or self.partner_bank_id.partner_id != self.partner_id:
                if self.partner_id.bank_ids:
                    self.partner_bank_id = self.partner_id.bank_ids[0]
                else:
                    self.partner_bank_id = False

    @api.onchange('partner_bank_id')
    def _onchange_partner_bank_id(self):
        if self.partner_bank_id:
            bank = self.partner_bank_id
            self.account_number = bank.acc_number or ''
            
            if bank.bank_id:
                self.bank_name = bank.bank_id.name

    @api.model
    @require_seerbit_auth
    def verify_account_api(self, account_number, bank_code):
        if not account_number or not bank_code:
            return {'account_name': False}
        api_client = SeerbitPocketAPI(self.env)
        try:
            res = api_client.account_enquiry(account_number, bank_code)
            _logger.info("Seerbit Enquiry returned data: %s", res)
            account_name = res.get('cutomername') or res.get('accountName') if res else None
            _logger.info("Parsed account_name: %s", account_name)
            
            if account_name:
                return {'account_name': account_name}
        except UserError:
            raise
        except Exception as e:
            _logger.error("Enquiry Exception: %s", e)
        return {'account_name': False}

    @api.model
    @require_seerbit_auth
    def get_seerbit_banks(self):
        param_obj = self.env['ir.config_parameter'].sudo()
        cached_banks_str = param_obj.get_param('pos_seerbit.cached_banks')
        
        api_client = SeerbitPocketAPI(self.env)
        try:
            banks = api_client.get_banks()
            if banks:
                import json
                param_obj.set_param('pos_seerbit.cached_banks', json.dumps(banks))
                return banks
        except Exception:
            pass
            
        if cached_banks_str:
            import json
            try:
                return json.loads(cached_banks_str)
            except Exception:
                pass
                
        return []

    @api.onchange('move_id')
    def _onchange_move_id(self):
        if self.move_id:
            if not self.partner_id or self.partner_id != self.move_id.partner_id:
                self.partner_id = self.move_id.partner_id
            self.amount = self.move_id.amount_residual
            if hasattr(self.move_id, 'partner_bank_id') and self.move_id.partner_bank_id:
                self.partner_bank_id = self.move_id.partner_bank_id
            elif self.partner_id and self.partner_id.bank_ids:
                self.partner_bank_id = self.partner_id.bank_ids[0]

    @require_seerbit_auth
    def action_get_otp(self):
        self.ensure_one()
        api_client = SeerbitPocketAPI(self.env)
        
        try:
            api_client.get_otp()
            self.state = 'pending_otp'
            self.last_otp_sent_time = fields.Datetime.now()
            
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('OTP Sent'),
                    'message': _('Please enter the OTP sent to your registered contact.'),
                    'type': 'success',
                    'sticky': False,
                    'next': {'type': 'ir.actions.act_window_close'},
                }
            }
        except Exception as e:
            raise UserError(_("Failed to request OTP: %s") % str(e))

    def action_reset_to_draft(self):
        for rec in self:
            rec.state = 'draft'

    @require_seerbit_auth
    def action_resend_otp(self):
        self.ensure_one()
        from datetime import timedelta
        if self.last_otp_sent_time and fields.Datetime.now() - self.last_otp_sent_time < timedelta(minutes=1):
            remaining = 60 - (fields.Datetime.now() - self.last_otp_sent_time).seconds
            raise UserError(_("Please wait %s seconds before requesting a new OTP.") % remaining)
            
        api_client = SeerbitPocketAPI(self.env)
        try:
            api_client.get_otp()
            self.last_otp_sent_time = fields.Datetime.now()
            
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('OTP Resent'),
                    'message': _('A new OTP has been sent to your registered contact.'),
                    'type': 'success',
                    'sticky': False,
                    'next': {'type': 'ir.actions.act_window_close'},
                }
            }
        except Exception as e:
            raise UserError(_("Failed to resend OTP: %s") % str(e))

    @require_seerbit_auth
    def action_submit_payout(self, otp=None):
        self.ensure_one()
        api_client = SeerbitPocketAPI(self.env)
        
        passkey = otp if otp else self.otp_code
        if not passkey:
            raise UserError(_("Please provide the OTP to process the payout."))
            
        try:
            res = api_client.submit_payout(
                amount=self.amount,
                bank_code=self.bank_code,
                account_number=self.account_number,
                account_name=self.account_name,
                reference=self.name,
                passkey=passkey,
                currency=self.currency_id.name,
                description=self.description
            )
            
            self.state = 'completed'
            self._create_odoo_payment()
            
        except Exception as e:
            if 'Duplicate payout reference' in str(e):
                _logger.info("Payout %s was already processed successfully on Seerbit. Marking as completed.", self.name)
                self.state = 'completed'
                self._create_odoo_payment()
                return
                
            self.state = 'failed'
            _logger.error("Payout failed: %s", str(e))
            raise UserError(_("Payout Failed: %s") % str(e))

    def _create_odoo_payment(self):
        self.ensure_one()
        if self.payment_id:
            return
            
        journal = self.env['account.journal'].search([('type', '=', 'bank'), ('name', 'ilike', 'Seerbit')], limit=1)
        if not journal:
            journal = self.env['account.journal'].search([('type', '=', 'bank')], limit=1)
            
        payment_method = self.env.ref('account.account_payment_method_manual_out')
        
        dest_account_id = False
        if self.move_id:
            bill_payable_line = self.move_id.line_ids.filtered(lambda l: l.account_id.account_type == 'liability_payable')
            if bill_payable_line:
                dest_account_id = bill_payable_line[0].account_id.id
                
        payment_method_line = journal.outbound_payment_method_line_ids.filtered(lambda l: l.payment_method_id == payment_method)[:1] or journal.outbound_payment_method_line_ids[:1]
        payment_vals = {
            'payment_type': 'outbound',
            'partner_type': 'supplier',
            'partner_id': self.partner_id.id if self.partner_id else False,
            'partner_bank_id': self.partner_bank_id.id if self.partner_bank_id else False,
            'amount': self.amount,
            'journal_id': journal.id,
            'payment_method_line_id': payment_method_line.id,
            'memo': self.name,
        }
        if dest_account_id:
            payment_vals['destination_account_id'] = dest_account_id
            
        outstanding_acc = payment_method_line.payment_account_id or journal.default_account_id or journal.company_id.transfer_account_id
        if outstanding_acc:
            payment_vals['force_outstanding_account_id'] = outstanding_acc.id
        
        payment = self.env['account.payment'].create(payment_vals)
        payment.action_post()
        
        # Auto-reconciliation
        if self.partner_id:
            payment_lines = payment.move_id.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable' or l.account_id.account_type == 'liability_payable')
            if payment_lines:
                payment_line = payment_lines[0]
                
                # Reconcile with specific bill
                if self.move_id:
                    try:
                        self.move_id.js_assign_outstanding_line(payment_line.id)
                    except Exception as e:
                        _logger.error("Failed to auto-reconcile bill: %s", str(e))
                else:
                    # Reconcile with oldest bills (FIFO)
                    unpaid_moves = self.env['account.move'].search([
                        ('partner_id', '=', self.partner_id.id),
                        ('move_type', '=', 'in_invoice'),
                        ('state', '=', 'posted'),
                        ('payment_state', 'in', ('not_paid', 'partial'))
                    ], order='invoice_date asc, id asc')
                    
                    for move in unpaid_moves:
                        if payment_line.reconciled:
                            break
                        try:
                            move.js_assign_outstanding_line(payment_line.id)
                        except Exception:
                            pass

        # Fully reconcile the payment with a bank statement to mark it as Paid
        try:
            liquidity_lines, _, _ = payment._seek_for_lines()
            if liquidity_lines:
                st_line = self.env['account.bank.statement.line'].create({
                    'payment_ref': payment.memo or 'Seerbit Payout',
                    'journal_id': payment.journal_id.id,
                    'amount': -payment.amount,
                    'date': payment.date,
                    'partner_id': payment.partner_id.id if payment.partner_id else False,
                })
                
                suspense_line = st_line.move_id.line_ids.filtered(lambda l: l.account_id == st_line.journal_id.suspense_account_id)
                if suspense_line:
                    suspense_line.account_id = liquidity_lines.account_id.id
                    (suspense_line | liquidity_lines).reconcile()
        except Exception as e:
            _logger.error("Failed to auto-reconcile bank statement for payout: %s", str(e))

        self.payment_id = payment.id

    def action_view_payment(self):
        self.ensure_one()
        if self.payment_id:
            return {
                'type': 'ir.actions.act_window',
                'name': _('Payment'),
                'res_model': 'account.payment',
                'view_mode': 'form',
                'res_id': self.payment_id.id,
                'target': 'current',
            }
        return False

    @api.model
    def authenticate_pocket(self, email, password):
        api_client = SeerbitPocketAPI(self.env)
        return api_client.authenticate(email, password)

    @api.model
    def authenticate_pocket_with_config(self):
        email = self.env['ir.config_parameter'].sudo().get_param('pos_seerbit.seerbit_pocket_email')
        password = self.env['ir.config_parameter'].sudo().get_param('pos_seerbit.seerbit_pocket_password')
        if email and password:
            try:
                api_client = SeerbitPocketAPI(self.env)
                res = api_client.authenticate(email, password)
                return bool(res)
            except Exception as e:
                _logger.warning("Config auto-auth failed: %s", e)
        return False

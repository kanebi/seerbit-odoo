from odoo import api, fields, models, _
from odoo.exceptions import UserError
import logging

from odoo.addons.pos_seerbit.bus_notify import send_seerbit_ui_notification

_logger = logging.getLogger(__name__)

class AccountMove(models.Model):
    _inherit = 'account.move'

    synced_with_seerbit = fields.Boolean(string='Synced with Seerbit', default=False, copy=False)
    seerbit_invoice_no = fields.Char(string='Seerbit Invoice No', copy=False, readonly=True)
    seerbit_invoice_status = fields.Char(string='Seerbit Status', copy=False, readonly=True)
    seerbit_terminal_id = fields.Char(string='Seerbit Terminal ID', copy=False)
    seerbit_payment_link_ids = fields.One2many('pos_seerbit.payment.link', 'move_id', string='Seerbit Payment Links')

    def action_post(self):
        res = super().action_post()
        for move in self.filtered(lambda m: m.synced_with_seerbit and m.seerbit_invoice_no and m.move_type == 'out_invoice'):
            from ..services.seerbit_api import SeerbitAPI
            api_client = SeerbitAPI(self.env, company=move.company_id)
            existing_invoice = api_client.get_invoice(move.seerbit_invoice_no)
            if existing_invoice:
                status = existing_invoice.get('status', '').upper()
                if status in ['PAID', 'SUCCESS']:
                    remote_amount = existing_invoice.get('amount') or existing_invoice.get('transactionValue') or existing_invoice.get('RequestedAmount')
                    if remote_amount is not None and round(float(remote_amount), 2) != round(move.amount_total, 2):
                        raise UserError(_("This invoice was modified to %s, but it has already been PAID on Seerbit for %s. Please revert your changes and create a new invoice for any discrepancies.") % (move.amount_total, remote_amount))
        return res


    def action_sync_seerbit_invoice(self):
        self.ensure_one()
        if self.move_type != 'out_invoice':
            raise UserError(_("Only Customer Invoices can be synced to Seerbit."))
            
        from ..services.seerbit_api import SeerbitAPI
        api_client = SeerbitAPI(self.env, company=self.company_id)
        
        # If already synced, check status before updating
        if self.seerbit_invoice_no:
            existing_invoice = api_client.get_invoice(self.seerbit_invoice_no)
            if existing_invoice:
                status = existing_invoice.get('status', '').upper()
                if status in ['PAID', 'SUCCESS']:
                    raise UserError(_("The Seerbit invoice is already paid. Please create changes on a new invoice instead of modifying this one."))
            # Attempt to delete the existing invoice on Seerbit before creating a new one
            api_client.delete_invoice(self.seerbit_invoice_no)
        
        # Prepare invoice items
        invoice_items = []
        for line in self.invoice_line_ids:
            if line.display_type == 'product':
                tax_amount = sum(line.tax_ids.mapped('amount')) if line.tax_ids else 0.0
                invoice_items.append({
                    "itemName": line.name or line.product_id.name,
                    "quantity": line.quantity,
                    "rate": line.price_unit,
                    "tax": tax_amount
                })
                
        # Handle cases where Seerbit might need at least 1 item
        if not invoice_items:
            invoice_items.append({
                "itemName": "General Description",
                "quantity": 1,
                "rate": self.amount_total,
                "tax": 0.0
            })

        due_date = self.invoice_date_due.strftime('%Y-%m-%d') if self.invoice_date_due else fields.Date.today().strftime('%Y-%m-%d')
        
        res = api_client.create_invoice(
            order_no=self.name or str(self.id),
            due_date=due_date,
            currency=self.currency_id.name,
            receivers_name=self.partner_id.name,
            customer_email=self.partner_id.email or 'no-email@example.com',
            invoice_items=invoice_items
        )
        
        self.write({
            'seerbit_invoice_no': res.get('InvoiceNo'),
            'synced_with_seerbit': True
        })

    def action_check_seerbit_status(self):
        for move in self:
            if not move.seerbit_invoice_no:
                continue
                
            from ..services.seerbit_api import SeerbitAPI
            api_client = SeerbitAPI(self.env, company=move.company_id)
            
            res = api_client.get_invoice(move.seerbit_invoice_no)
            if res:
                # Update status if needed (e.g., if Seerbit API returns PAID)
                status = res.get('status', '').upper()
                status_changed = move.seerbit_invoice_status != status
                move.seerbit_invoice_status = status
                
                if status in ['PAID', 'SUCCESS'] and move.payment_state in ['not_paid', 'partial']:
                    seerbit_co = self.env['res.company'].seerbit_company(move.company_id)
                    auto_post = seerbit_co.seerbit_auto_post
                    auto_reconcile = seerbit_co.seerbit_auto_reconcile
                    # Mark the invoice as paid by creating a payment and reconciling it
                    journal = self.env['account.journal'].search([('type', '=', 'bank'), ('name', 'ilike', 'Seerbit'), ('company_id', '=', move.company_id.id)], limit=1)
                    if not journal:
                        journal = self.env['account.journal'].search([('type', '=', 'bank'), ('company_id', '=', move.company_id.id)], limit=1)
                        
                    payment_method = self.env.ref('account.account_payment_method_manual_in')
                    
                    invoice_receivable_line = move.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable')
                    dest_account_id = invoice_receivable_line[0].account_id.id if invoice_receivable_line else False
                    
                    existing_payment = self.env['account.payment'].search([('memo', '=', f"Sync: {move.seerbit_invoice_no}")], limit=1)
                    if existing_payment:
                        payment = existing_payment
                        if payment.state == 'draft':
                            if auto_post:
                                payment.action_post()
                    else:
                        paid_amount = float(res.get('totalAmount')) if res.get('totalAmount') is not None else move.amount_residual
                        payment_method_line = journal.inbound_payment_method_line_ids.filtered(
                        lambda l: l.payment_method_id == payment_method
                    )[:1] or journal.inbound_payment_method_line_ids[:1]

                        payment_vals = {
                            'payment_type': 'inbound',
                            'partner_type': 'customer',
                            'partner_id': move.partner_id.id,
                            'amount': paid_amount,
                            'journal_id': journal.id,
                            'company_id': move.company_id.id,
                            'payment_method_line_id': payment_method_line.id,
                            'memo': f"Sync: {move.seerbit_invoice_no}",
                        }
                        if dest_account_id:
                            payment_vals['destination_account_id'] = dest_account_id
                            
                        outstanding_acc = payment_method_line.payment_account_id or journal.default_account_id or journal.company_id.transfer_account_id
                        if outstanding_acc:
                            payment_vals['force_outstanding_account_id'] = outstanding_acc.id
                            
                        payment = self.env['account.payment'].create(payment_vals)
                        if auto_post:
                            payment.action_post()
                    
                    # Reconcile specifically with this invoice
                    if auto_post and auto_reconcile:
                        payment_lines = payment.move_id.line_ids.filtered(lambda line: line.account_id.account_type == 'asset_receivable' and not line.reconciled)
                        if payment_lines:
                            try:
                                move.js_assign_outstanding_line(payment_lines[0].id)
                            except Exception as e:
                                _logger.error(f"Failed to auto-reconcile payment for invoice {move.name}: {e}")
                                
                        if payment.state == 'in_process':
                            move.partner_id.sudo()._reconcile_seerbit_payment(payment)
                    
                    send_seerbit_ui_notification(
                        self.env,
                        'Seerbit Payment',
                        f'Invoice {move.name} was paid on Seerbit',
                        record=move,
                    )
                elif status_changed:
                    send_seerbit_ui_notification(
                        self.env,
                        'Seerbit Status Update',
                        f'Invoice {move.name} status is now {status}',
                        record=move,
                    )

    def action_check_all_seerbit_status(self):
        invoices = self.search([
            ('synced_with_seerbit', '=', True),
            ('state', '=', 'posted'),
            '|',
            ('seerbit_invoice_status', '=', False),
            '|',
            ('seerbit_invoice_status', 'not in', ['PAID', 'SUCCESS']),
            '&',
            ('seerbit_invoice_status', 'in', ['PAID', 'SUCCESS']),
            ('payment_state', 'in', ['not_paid', 'partial']),
        ])
        for invoice in invoices:
            try:
                invoice.action_check_seerbit_status()
            except Exception as e:
                _logger.error(f"Error in Seerbit cron for invoice {invoice.name}: {e}")
                send_seerbit_ui_notification(
                    self.env,
                    'Seerbit Sync Error',
                    f'Failed to sync invoice {invoice.name}: {str(e)}',
                    record=invoice,
                )

    def action_send_payment_to_pos(self):
        self.ensure_one()
        # "Pushes the outstanding/due amount based on terms of payment"
        amount_due = self.amount_residual
        if amount_due <= 0:
            raise UserError(_("There is no outstanding amount to send to POS."))
            
        pos_methods = self.env['pos.payment.method'].search([
            ('use_payment_terminal', '=', 'seerbit'),
            ('company_id', '=', self.company_id.id),
        ])
        if not pos_methods:
            raise UserError(_("No Seerbit POS payment method configured."))
            
        # Return action to open the wizard in the UI
        return {
            'name': _('Select POS Terminal'),
            'type': 'ir.actions.act_window',
            'res_model': 'seerbit.invoice.payment.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_invoice_id': self.id,
                'default_pos_payment_method_id': pos_methods[0].id if len(pos_methods) == 1 else False,
            }
        }

    def action_process_seerbit_pos_payment(self, transaction_ref, amount):
        self.ensure_one()
        
        # Find Seerbit Bank Journal
        journal = self.env['account.journal'].search([('type', '=', 'bank'), ('name', 'ilike', 'Seerbit'), ('company_id', '=', self.company_id.id)], limit=1)
        if not journal:
            journal = self.env['account.journal'].search([('type', '=', 'bank'), ('company_id', '=', self.company_id.id)], limit=1)
            
        payment_method = self.env.ref('account.account_payment_method_manual_in')
        
        # Ensure we don't process the same transaction reference multiple times
        existing_payment = self.env['account.payment'].search([('move_id.ref', '=', transaction_ref)], limit=1)
        if existing_payment:
            # Reconcile if not already reconciled
            payment_lines = existing_payment.move_id.line_ids.filtered(lambda line: line.account_id.account_type == 'asset_receivable' and not line.reconciled)
            if payment_lines:
                try:
                    self.js_assign_outstanding_line(payment_lines[0].id)
                except Exception as e:
                    _logger.error(f"Failed to auto-reconcile payment for invoice {self.name}: {e}")
            return True
            
        invoice_receivable_line = self.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable')
        dest_account_id = invoice_receivable_line[0].account_id.id if invoice_receivable_line else False
        
        payment_vals = {
            'payment_type': 'inbound',
            'partner_type': 'customer',
            'partner_id': self.partner_id.id,
            'amount': float(amount or self.amount_residual),
            'journal_id': journal.id,
            'company_id': self.company_id.id,
            'payment_method_line_id': journal.inbound_payment_method_line_ids.filtered(lambda l: l.payment_method_id == payment_method)[:1].id or journal.inbound_payment_method_line_ids[:1].id,
            'memo': transaction_ref,
        }
        if dest_account_id:
            payment_vals['destination_account_id'] = dest_account_id
        payment = self.env['account.payment'].create(payment_vals)
        seerbit_co = self.env['res.company'].seerbit_company(self.company_id)
        auto_post = seerbit_co.seerbit_auto_post
        auto_reconcile = seerbit_co.seerbit_auto_reconcile
        if auto_post:
            payment.action_post()
        
        # Reconcile specifically with this invoice
        if auto_post and auto_reconcile:
            payment_lines = payment.move_id.line_ids.filtered(lambda line: line.account_id.account_type == 'asset_receivable' and not line.reconciled)
            if payment_lines:
                try:
                    self.js_assign_outstanding_line(payment_lines[0].id)
                except Exception as e:
                    _logger.error(f"Failed to auto-reconcile payment for invoice {self.name}: {e}")
                    
            if payment.state == 'in_process':
                self.partner_id.sudo()._reconcile_seerbit_payment(payment)
                
        self.message_post(body=f"Seerbit POS Payment of {amount} processed successfully. Reference: {transaction_ref}")
            
        return True

    def action_resend_seerbit_pos_payment(self, terminal_id=None):
        self.ensure_one()
        t_id = terminal_id if (terminal_id and terminal_id != 'undefined') else self.seerbit_terminal_id
        if not t_id:
            # Fallback: search for any Seerbit payment terminal configured
            pos_method = self.env['pos.payment.method'].search([('use_payment_terminal', '=', 'seerbit'), ('seerbit_terminal_id', '!=', False)], limit=1)
            if pos_method:
                t_id = pos_method.seerbit_terminal_id
        if not t_id:
            raise UserError(_("No Seerbit terminal ID specified or found for this transaction."))

        pos_method = self.env['pos.payment.method'].search([('seerbit_terminal_id', '=', t_id)], limit=1)
        if not pos_method:
            raise UserError(_("POS Terminal with ID %s not found.") % t_id)

        import json

        metadata = json.dumps({
            'created_by': 'odoo_pos_seerbit',
            'created_time': fields.Datetime.now().isoformat() + 'Z',
            'invoice_id': str(self.id),
            'user_id': str(self.env.user.id),
            'payment_method_id': str(pos_method.id),
            'company_id': str(self.company_id.id),
        })

        payload = {
            'id': str(self.id),
            'posid': str(pos_method.seerbit_terminal_id),
            'merchantid': '',
            'metadata': metadata,
            'transactionValue': '%.2f' % self.amount_residual,
            'status': 'open',
            'transactionTime': '',
            'sessionId': '',
            'receivedDateTime': fields.Datetime.now().strftime("%d/%m/%Y %H:%M"),
            'transactionRef': '',
            'pubkey': str(pos_method.seerbit_public_key),
        }

        # Log constructed payload
        _logger.info("Resending payment request to POS from Invoice Action: %s", payload)

        pos_method.send_seerbit_payment_request(payload)
        return True

# coding: utf-8
from odoo import models, fields, api, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)

class SeerbitInvoicePaymentWizard(models.TransientModel):
    _name = 'seerbit.invoice.payment.wizard'
    _description = 'Seerbit Invoice Payment Wizard'

    invoice_id = fields.Many2one('account.move', string='Invoice', required=True, readonly=True)
    amount_due = fields.Monetary(related='invoice_id.amount_residual', string='Amount Due', currency_field='currency_id', readonly=True)
    currency_id = fields.Many2one('res.currency', related='invoice_id.currency_id', readonly=True)
    
    pos_payment_method_id = fields.Many2one(
        'pos.payment.method',
        string='POS Terminal',
        required=True,
        domain="[('use_payment_terminal', '=', 'seerbit')]"
    )

    def action_confirm_payment(self):
        self.ensure_one()
        invoice = self.invoice_id
        pos_method = self.pos_payment_method_id

        if invoice.amount_residual <= 0:
            raise UserError(_("There is no outstanding amount to send to POS."))

        import json

        metadata = json.dumps({
            'created_by': 'odoo_pos_seerbit',
            'created_time': fields.Datetime.now().isoformat() + 'Z',
            'invoice_id': str(invoice.id),
            'user_id': str(self.env.user.id),
            'payment_method_id': str(pos_method.id),
            'company_id': str(invoice.company_id.id),
        })

        payload = {
            'id': str(invoice.id),
            'posid': str(pos_method.seerbit_terminal_id),
            'merchantid': '',
            'metadata': metadata,
            'transactionValue': '%.2f' % invoice.amount_residual,
            'status': 'open',
            'transactionTime': '',
            'sessionId': '',
            'receivedDateTime': fields.Datetime.now().strftime("%d/%m/%Y %H:%M"),
            'transactionRef': '',
            'pubkey': str(pos_method.seerbit_public_key),
        }

        # Log constructed payload
        _logger.info("Sending payment request to POS from Wizard: %s", payload)

        # Persist selected terminal ID on the invoice itself
        invoice.write({'seerbit_terminal_id': pos_method.seerbit_terminal_id})

        # Uses the existing Firestore push logic
        pos_method.send_seerbit_payment_request(payload)

        # Return client action to listen for the payment reconciliation
        return {
            'type': 'ir.actions.client',
            'tag': 'seerbit_invoice_reconciliation_listener',
            'params': {
                'invoice_id': invoice.id,
                'terminal_id': pos_method.seerbit_terminal_id,
            }
        }

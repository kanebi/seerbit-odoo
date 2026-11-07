# -*- coding: utf-8 -*-
from odoo import models


class PosSession(models.Model):
    _inherit = "pos.session"

    def _broadcast_seerbit_va_event(self, event_type, data):
        """
        Broadcasts notification over bus.bus channel dedicated to this POS session.
        """
        self.ensure_one()
        channel = f"pos_seerbit_va_{self.id}"
        self.env['bus.bus']._sendone(
            channel,
            'seerbit_va_notification',
            {
                'type': event_type,
                'session_id': self.id,
                'data': data
            }
        )

    def _create_split_account_payment(self, payment, amounts):
        res = super()._create_split_account_payment(payment, amounts)
        if payment.seerbit_transaction_ref or payment.seerbit_va_transaction_id:
            account_payments = self.env['account.payment'].search([
                ('pos_session_id', '=', self.id),
                ('pos_payment_method_id', '=', payment.payment_method_id.id),
            ], order='id desc', limit=1)
            if account_payments:
                vals = {}
                if payment.seerbit_transaction_ref and payment.seerbit_transaction_ref not in (account_payments.memo or ''):
                    vals['memo'] = f"{account_payments.memo or ''} - Ref: {payment.seerbit_transaction_ref}".strip(' -')
                if payment.seerbit_va_transaction_id and payment.seerbit_va_transaction_id.va_id:
                    vals['seerbit_va_id'] = payment.seerbit_va_transaction_id.va_id.id
                if vals:
                    account_payments.write(vals)
        return res

# -*- coding: utf-8 -*-
import datetime
import logging
from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PosOrder(models.Model):
    _inherit = 'pos.order'

    seerbit_va_transaction_id = fields.Many2one(
        'seerbit.va.transaction',
        string="SeerBit VA Transaction",
        copy=False,
        readonly=True
    )
    seerbit_transaction_ref = fields.Char(
        string="SeerBit Reference",
        copy=False,
        readonly=True,
        index=True
    )
    seerbit_payer_name = fields.Char(
        string="SeerBit Payer Name",
        copy=False,
        readonly=True
    )
    seerbit_amount_received = fields.Monetary(
        string="SeerBit Received Amount",
        copy=False,
        readonly=True
    )

    @api.model
    def _attempt_va_auto_match(self, transaction):
        """
        Attempts to automatically match an incoming VA transaction with an active POS order.
        Criteria:
          1. Company matches.
          2. Payment method uses SeerBit Transfer with the transaction's VA.
          3. POS session is currently 'opened'.
          4. Open order's remaining amount equals transaction.amount exactly.
          5. Order creation timestamp is within match window (default 30 mins) before payment.
          6. Exactly ONE matching order exists (ambiguity safety rule).
        """
        company = transaction.company_id
        window_minutes = company.seerbit_pos_va_match_window_minutes or 30

        # 1. Identify open POS sessions with payment methods configured for this VA
        if transaction.va_id.va_type == 'pos_static':
            payment_methods = self.env['pos.payment.method'].search([
                ('company_id', '=', company.id),
                ('payment_method_type', '=', 'transfer'),
                ('seerbit_va_id', '=', transaction.va_id.id),
            ])
        else:
            payment_methods = self.env['pos.payment.method'].search([
                ('company_id', '=', company.id),
                ('payment_method_type', '=', 'transfer'),
                ('seerbit_transfer_type', '=', 'dynamic'),
            ])
        if not payment_methods:
            return False

        # 2. Query active POS sessions
        open_sessions = self.env['pos.session'].search([
            ('company_id', '=', company.id),
            ('state', '=', 'opened'),
        ])
        if not open_sessions:
            return False

        # 3. Query candidate POS orders in draft / awaiting payment state
        min_order_time = transaction.payment_date - datetime.timedelta(minutes=window_minutes)
        candidate_orders = self.search([
            ('session_id', 'in', open_sessions.ids),
            ('state', 'in', ['draft']),
            ('date_order', '>=', min_order_time),
            ('amount_total', '=', transaction.amount),
        ])

        # 4. Strict Unambiguity Check (Dynamic VAs match directly on order reference)
        if transaction.va_id.va_type == 'pos_dynamic':
            for order in candidate_orders:
                clean_ref = (order.pos_reference or order.name or '').replace(' ', '').replace('-', '_')
                if clean_ref and clean_ref in transaction.reference:
                    candidate_orders = order
                    break

        if len(candidate_orders) == 1:
            matched_order = candidate_orders[0]
            now = fields.Datetime.now()
            transaction.write({
                'matching_status': 'auto_matched',
                'pos_order_id': matched_order.id,
                'pos_order_ref': matched_order.pos_reference or matched_order.name,
                'pos_session_id': matched_order.session_id.id,
                'matched_at': now,
            })

            # Broadcast auto-matched event to the POS session
            matched_order.session_id._broadcast_seerbit_va_event(
                event_type='seerbit_va_payment_matched',
                data={
                    'transaction_id': transaction.id,
                    'reference': transaction.reference,
                    'amount': transaction.amount,
                    'payer_name': transaction.payer_name,
                    'order_id': matched_order.id,
                    'order_ref': matched_order.pos_reference or matched_order.name,
                }
            )
            return True

        # 5. Ambiguous or No Match: Leave unmatched & notify cashiers for manual pick
        for session in open_sessions:
            session._broadcast_seerbit_va_event(
                event_type='seerbit_va_payment_received',
                data={
                    'transaction_id': transaction.id,
                    'reference': transaction.reference,
                    'amount': transaction.amount,
                    'payer_name': transaction.payer_name,
                    'account_number': transaction.account_number,
                    'payment_date': fields.Datetime.to_string(transaction.payment_date),
                }
            )
        return False

    def action_pos_order_paid(self):
        for order in self:
            for payment in order.payment_ids:
                if payment.payment_method_id.payment_method_type == 'transfer':
                    if not payment.seerbit_va_transaction_id and not payment.seerbit_transaction_ref:
                        raise UserError(
                            _("Order %s cannot be marked as paid: Bank transfer payment (%s) has not been confirmed or matched.")
                            % (order.pos_reference or order.name, payment.payment_method_id.name)
                        )
        return super().action_pos_order_paid()

    def _process_saved_order(self, draft):
        for order in self:
            if not draft:
                for payment in order.payment_ids:
                    if payment.payment_method_id.payment_method_type == 'transfer':
                        if not payment.seerbit_va_transaction_id and not payment.seerbit_transaction_ref:
                            raise UserError(
                                _("Order %s cannot be completed: Bank transfer payment (%s) has not been confirmed or matched.")
                                % (order.pos_reference or order.name, payment.payment_method_id.name)
                            )
        res = super()._process_saved_order(draft)
        for order in self:
            for payment in order.payment_ids:
                if payment.seerbit_va_transaction_id or payment.seerbit_transaction_ref:
                    order.write({
                        'seerbit_va_transaction_id': payment.seerbit_va_transaction_id.id if payment.seerbit_va_transaction_id else False,
                        'seerbit_transaction_ref': payment.seerbit_transaction_ref,
                        'seerbit_payer_name': payment.seerbit_payer_name,
                        'seerbit_amount_received': payment.amount,
                    })
                    if payment.seerbit_va_transaction_id:
                        tx_vals = {
                            'pos_order_id': order.id,
                            'pos_order_ref': order.pos_reference or order.name,
                            'pos_payment_id': payment.id,
                            'pos_session_id': order.session_id.id,
                        }
                        if payment.seerbit_va_transaction_id.matching_status == 'unmatched':
                            tx_vals['matching_status'] = 'manual_matched'
                            tx_vals['matched_at'] = fields.Datetime.now()
                        payment.seerbit_va_transaction_id.write(tx_vals)
                    break
        return res


from odoo.tools import float_is_zero
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
    seerbit_va_transaction_ids = fields.One2many(
        'seerbit.va.transaction',
        'pos_order_id',
        string="SeerBit Transactions",
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

        # 2. Query active POS sessions configured with this payment method
        open_sessions = self.env['pos.session'].search([
            ('company_id', '=', company.id),
            ('state', '=', 'opened'),
            ('config_id.payment_method_ids', 'in', payment_methods.ids),
        ])
        if not open_sessions:
            return False

        # 3. Check for duplicate unmatched payments with same amount on this VA (Ambiguity Safety)
        # Only applies to static VAs where account is shared across orders. Dynamic VAs have dedicated accounts.
        if transaction.va_id.va_type == 'pos_static':
            same_amount_unmatched = self.env['seerbit.va.transaction'].search([
                ('va_id', '=', transaction.va_id.id),
                ('matching_status', '=', 'unmatched'),
                ('amount', '=', transaction.amount),
                ('status', '=', 'successful'),
            ])
            if len(same_amount_unmatched) > 1:
                _logger.info(
                    "Multiple unmatched transactions (%s) found with amount %s for VA %s. "
                    "Refusing auto-match to prevent wrong attribution; manual match required.",
                    len(same_amount_unmatched), transaction.amount, transaction.va_id.display_name
                )
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
                            'has_multiple_same_amount': True,
                            'requires_manual_match': True,
                        }
                    )
                return False

        # 4. Query candidate POS orders in draft / awaiting payment state
        min_order_time = transaction.payment_date - datetime.timedelta(minutes=window_minutes)
        order_domain = [
            ('session_id', 'in', open_sessions.ids),
            ('state', 'in', ['draft']),
            ('date_order', '>=', min_order_time),
        ]
        if not company.seerbit_pos_va_allow_partial:
            order_domain.append(('amount_total', '=', transaction.amount))
        else:
            order_domain.append(('amount_total', '>=', transaction.amount))
        candidate_orders = self.search(order_domain)

        # 4. Strict Unambiguity Check (Dynamic VAs match directly on order reference)
        if transaction.va_id.va_type == 'pos_dynamic':
            matched = self.env['pos.order']
            for order in candidate_orders:
                clean_ref = (order.pos_reference or order.name or '').replace(' ', '').replace('-', '_')
                if clean_ref and clean_ref in transaction.reference:
                    matched = order
                    break
            if matched:
                candidate_orders = matched

        if len(candidate_orders) == 1:
            matched_order = candidate_orders[0]
            now = fields.Datetime.now()
            transaction.write({
                'matching_status': 'auto_matched',
                'pos_order_id': matched_order.id,
                'pos_order_ref': matched_order.pos_reference or matched_order.name,
                'pos_session_id': matched_order.session_id.id,
                'matched_by_user_id': matched_order.user_id.id or self.env.uid,
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
                    'account_number': transaction.account_number,
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
                        if not payment.seerbit_va_transaction_id and not payment.seerbit_transaction_ref and not payment.transaction_id:
                            raise UserError(
                                _("Order %s cannot be completed: Bank transfer payment (%s) has not been confirmed or matched.")
                                % (order.pos_reference or order.name, payment.payment_method_id.name)
                            )
        res = super()._process_saved_order(draft)
        for order in self:
            candidate_refs = set(filter(None, [
                order.pos_reference,
                order.name,
                getattr(order, 'uuid', None),
            ]))
            for payment in order.payment_ids:
                if payment.seerbit_transaction_ref:
                    for r in str(payment.seerbit_transaction_ref).split(','):
                        r_clean = r.strip()
                        if r_clean:
                            candidate_refs.add(r_clean)
                if payment.transaction_id:
                    candidate_refs.add(payment.transaction_id)
                if payment.payment_ref_no:
                    candidate_refs.add(payment.payment_ref_no)
                if payment.seerbit_va_transaction_id:
                    candidate_refs.add(payment.seerbit_va_transaction_id.reference)

            matched_txs = self.env['seerbit.va.transaction'].search([
                '|',
                ('pos_order_ref', 'in', list(candidate_refs)),
                ('reference', 'in', list(candidate_refs)),
            ])

            transfer_payments = order.payment_ids.filtered(
                lambda p: p.payment_method_id.payment_method_type == 'transfer' or p.seerbit_transaction_ref or p.transaction_id
            )
            primary_payment = transfer_payments[:1] if transfer_payments else False

            if matched_txs:
                total_received = sum(matched_txs.mapped('amount'))
                refs_list = matched_txs.mapped('reference')
                refs_str = ", ".join(refs_list)
                payers_list = list(filter(None, set(matched_txs.mapped('payer_name'))))
                payers_str = ", ".join(payers_list) or 'Bank Customer'

                order.write({
                    'seerbit_va_transaction_id': matched_txs[0].id,
                    'seerbit_transaction_ref': refs_str,
                    'seerbit_payer_name': payers_str,
                    'seerbit_amount_received': total_received,
                })

                now = fields.Datetime.now()
                for tx in matched_txs:
                    tx_vals = {
                        'pos_order_id': order.id,
                        'pos_order_ref': order.pos_reference or order.name,
                        'pos_session_id': order.session_id.id,
                    }
                    if primary_payment:
                        tx_vals['pos_payment_id'] = primary_payment.id
                    if not tx.matched_by_user_id:
                        tx_vals['matched_by_user_id'] = order.user_id.id or self.env.uid
                    if tx.va_id.va_type == 'pos_dynamic' or tx.matching_status == 'unmatched':
                        tx_vals['matching_status'] = 'auto_matched'
                        if not tx.matched_at:
                            tx_vals['matched_at'] = now
                    elif not tx.matched_at:
                        tx_vals['matched_at'] = now
                    tx.write(tx_vals)

                if primary_payment:
                    primary_payment.write({
                        'seerbit_va_transaction_id': matched_txs[0].id,
                        'seerbit_transaction_ref': refs_str,
                        'transaction_id': refs_list[0] if refs_list else False,
                        'payment_ref_no': refs_str,
                        'cardholder_name': payers_str,
                    })
        return res


    @api.model
    def _process_payment_lines(self, pos_order, order, pos_session, draft):
        prec_acc = order.currency_id.decimal_places
        if not draft and not float_is_zero(pos_order.get('amount_return', 0.0), prec_acc):
            cash_payment_method = pos_session.payment_method_ids.filtered('is_cash_count')[:1]
            if not cash_payment_method:
                # Session has no cash payment method. If this order has any transfer payment,
                # zero out amount_return so Odoo does not raise UserError("No cash statement found for this session.")
                transfer_pms = pos_session.payment_method_ids.filtered(lambda m: m.payment_method_type == 'transfer')
                has_transfer = any(
                    line[2].get('payment_method_id') in transfer_pms.ids
                    for line in pos_order.get('lines', [])
                    if isinstance(line, (list, tuple)) and len(line) > 2 and isinstance(line[2], dict)
                ) or any(p.payment_method_id.payment_method_type == 'transfer' for p in order.payment_ids)
                if has_transfer:
                    _logger.info(
                        "Session %s has no cash payment method. Adjusting amount_return from %s to 0.0 for order %s to prevent 'No cash statement' UserError.",
                        pos_session.name, pos_order.get('amount_return'), order.pos_reference or order.name
                    )
                    pos_order['amount_return'] = 0.0
        return super()._process_payment_lines(pos_order, order, pos_session, draft)

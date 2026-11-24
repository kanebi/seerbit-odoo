# -*- coding: utf-8 -*-
import datetime
import json
import logging
import psycopg2
from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class SeerbitVaTransaction(models.Model):
    _name = 'seerbit.va.transaction'
    _description = 'SeerBit Virtual Account Inbound Transaction'
    _order = 'payment_date desc, id desc'

    name = fields.Char(
        string="Transaction Number",
        required=True,
        copy=False,
        default=lambda self: _('New')
    )
    va_id = fields.Many2one(
        'seerbit.virtual.account',
        string="Virtual Account",
        required=True,
        index=True,
        ondelete='cascade'
    )
    company_id = fields.Many2one(
        'res.company',
        string="Company",
        related='va_id.company_id',
        store=True,
        index=True
    )
    account_number = fields.Char(
        string="Account Number",
        related='va_id.account_number',
        store=True,
        index=True
    )
    reference = fields.Char(
        string="SeerBit Reference",
        required=True,
        copy=False,
        index=True
    )
    amount = fields.Monetary(
        string="Amount Received",
        currency_field='currency_id',
        required=True
    )
    currency_id = fields.Many2one(
        'res.currency',
        string="Currency",
        related='company_id.currency_id',
        store=True
    )
    payer_name = fields.Char(string="Payer / Customer Name")
    payment_date = fields.Datetime(
        string="Payment Date & Time",
        default=fields.Datetime.now,
        index=True
    )
    status = fields.Selection([
        ('pending', 'Pending'),
        ('successful', 'Successful'),
        ('failed', 'Failed'),
        ('reversed', 'Reversed')
    ], default='successful', required=True, index=True)

    matching_status = fields.Selection([
        ('unmatched', 'Unmatched'),
        ('auto_matched', 'Auto-Matched'),
        ('manual_matched', 'Manually Matched'),
        ('transferred', 'Transferred to Another Till'),
        ('cancelled', 'Cancelled')
    ], default='unmatched', required=True, index=True)

    transferred_to_config_id = fields.Many2one(
        'pos.config',
        string="Transferred To Till",
        readonly=True,
        index=True
    )
    transferred_to_va_id = fields.Many2one(
        'seerbit.virtual.account',
        string="Transferred To VA",
        readonly=True
    )
    transferred_to_tx_id = fields.Many2one(
        'seerbit.va.transaction',
        string="Destination Transaction",
        readonly=True
    )
    transferred_from_tx_id = fields.Many2one(
        'seerbit.va.transaction',
        string="Source Transaction",
        readonly=True
    )
    is_transferred_in = fields.Boolean(
        string="Is Transferred In",
        default=False,
        readonly=True
    )

    pos_order_id = fields.Many2one(
        'pos.order',
        string="Matched POS Order",
        index=True,
        copy=False,
        readonly=True
    )
    pos_order_ref = fields.Char(
        string="Order Reference",
        copy=False,
        readonly=True
    )
    pos_session_id = fields.Many2one(
        'pos.session',
        string="POS Session",
        readonly=True
    )
    pos_payment_id = fields.Many2one(
        'pos.payment',
        string="POS Payment Line",
        readonly=True
    )
    matched_by_user_id = fields.Many2one(
        'res.users',
        string="Matched By",
        readonly=True
    )
    matched_at = fields.Datetime(
        string="Matched At",
        readonly=True
    )
    raw_payload = fields.Text(string="Raw Webhook Payload")

    _seerbit_reference_unique = models.Constraint(
        'unique(reference)',
        "The SeerBit transaction reference must be unique to prevent duplicate processing."
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('seerbit.va.transaction') or vals.get('reference') or _('New')
        return super().create(vals_list)

    @api.model
    def register_inbound_transaction(self, va, reference, amount, payer_name, raw_payload=None, payment_date=None, skip_auto_match=False):
        """
        Idempotent creation or retrieval of inbound VA transaction.
        Triggers POS auto-matching engine if va_type is pos_static or pos_dynamic and skip_auto_match is False.
        """
        existing = self.search([('reference', '=', reference)], limit=1)
        if existing:
            if payer_name and payer_name != 'Bank Customer' and existing.payer_name == 'Bank Customer':
                existing.write({'payer_name': payer_name})
            # If an existing payment line references this, link it, but NEVER re-broadcast old notifications
            if existing.matching_status == 'unmatched' and va.va_type in ('pos_static', 'pos_dynamic'):
                payment = self.env['pos.payment'].search([
                    '|',
                    ('transaction_id', '=', reference),
                    ('seerbit_transaction_ref', '=', reference)
                ], limit=1)
                if payment and payment.pos_order_id:
                    self._match_transaction_to_payment(existing, payment, payer_name, amount)
            return existing

        vals = {
            'name': self.env['ir.sequence'].next_by_code('seerbit.va.transaction') or reference,
            'va_id': va.id,
            'reference': reference,
            'amount': float(amount or 0.0),
            'payer_name': payer_name or 'Bank Customer',
            'payment_date': payment_date or fields.Datetime.now(),
            'status': 'successful',
            'matching_status': 'unmatched',
            'raw_payload': json.dumps(raw_payload) if isinstance(raw_payload, dict) else str(raw_payload or ''),
        }
        try:
            with self.env.cr.savepoint():
                transaction = self.create(vals)
        except (psycopg2.IntegrityError, Exception) as e:
            _logger.info("register_inbound_transaction savepoint fallback for ref %s: %s", reference, e)
            existing = self.search([('reference', '=', reference)], limit=1)
            if existing:
                if payer_name and payer_name != 'Bank Customer' and existing.payer_name == 'Bank Customer':
                    existing.write({'payer_name': payer_name})
                # If an existing payment line references this, link it, but NEVER re-broadcast old notifications
                if existing.matching_status == 'unmatched' and va.va_type in ('pos_static', 'pos_dynamic'):
                    payment = self.env['pos.payment'].search([
                        '|',
                        ('transaction_id', '=', reference),
                        ('seerbit_transaction_ref', '=', reference)
                    ], limit=1)
                    if payment and payment.pos_order_id:
                        self._match_transaction_to_payment(existing, payment, payer_name, amount)
                return existing
            raise

        if va.va_type in ('pos_static', 'pos_dynamic'):
            # Check if an existing pos.payment or pos.order already references this transaction
            payment = self.env['pos.payment'].search([
                '|',
                ('transaction_id', '=', reference),
                ('seerbit_transaction_ref', '=', reference)
            ], limit=1)
            if payment and payment.pos_order_id:
                self._match_transaction_to_payment(transaction, payment, payer_name, amount)
            elif not skip_auto_match:
                # Attempt auto-match against open orders
                self.env['pos.order']._attempt_va_auto_match(transaction)
            else:
                self._broadcast_unmatched_received(transaction, has_multiple_same_amount=True)

        return transaction

    @api.model
    def _broadcast_unmatched_received(self, transaction, has_multiple_same_amount=False):
        va = transaction.va_id
        company = va.company_id
        if va.va_type == 'pos_static':
            payment_methods = self.env['pos.payment.method'].search([
                ('company_id', '=', company.id),
                ('payment_method_type', '=', 'transfer'),
                ('seerbit_va_id', '=', va.id),
            ])
        else:
            payment_methods = self.env['pos.payment.method'].search([
                ('company_id', '=', company.id),
                ('payment_method_type', '=', 'transfer'),
                ('seerbit_transfer_type', '=', 'dynamic'),
            ])
        open_sessions = self.env['pos.session'].search([
            ('company_id', '=', company.id),
            ('state', '=', 'opened'),
            ('config_id.payment_method_ids', 'in', payment_methods.ids),
        ])
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
                    'has_multiple_same_amount': has_multiple_same_amount,
                    'requires_manual_match': has_multiple_same_amount,
                }
            )

    @api.model
    def _match_transaction_to_payment(self, transaction, payment, payer_name=None, amount=None):
        order = payment.pos_order_id
        now = fields.Datetime.now()
        transaction.write({
            'matching_status': 'auto_matched' if transaction.va_id.va_type == 'pos_dynamic' else 'manual_matched',
            'pos_order_id': order.id,
            'pos_order_ref': order.pos_reference or order.name,
            'pos_payment_id': payment.id,
            'pos_session_id': order.session_id.id,
            'matched_by_user_id': order.user_id.id or self.env.uid,
            'matched_at': now,
        })
        payment.write({'seerbit_va_transaction_id': transaction.id})
        if not order.seerbit_va_transaction_id:
            order.write({
                'seerbit_va_transaction_id': transaction.id,
                'seerbit_transaction_ref': transaction.reference,
                'seerbit_payer_name': payer_name or transaction.payer_name or 'Bank Customer',
                'seerbit_amount_received': float(amount or transaction.amount or 0.0),
            })
        if order.session_id:
            order.session_id._broadcast_seerbit_va_event(
                event_type='seerbit_va_payment_matched',
                data={
                    'transaction_id': transaction.id,
                    'reference': transaction.reference,
                    'amount': transaction.amount,
                    'payer_name': transaction.payer_name,
                    'order_id': order.id,
                    'order_ref': order.pos_reference or order.name,
                    'account_number': transaction.account_number,
                }
            )

    @api.model
    def get_unmatched_payments_for_pos(self, va_id, order_created_at_str=None, limit=20, account_number=None):
        """
        RPC method called by POS Cashier to view available incoming payments.
        Lists unmatched payments for this virtual account.
        Matches by va_id and/or account_number to prevent drops from duplicate VA records.
        """
        if isinstance(va_id, (list, tuple)):
            va_id = va_id[0] if va_id else False
        elif isinstance(va_id, dict):
            va_id = va_id.get('id')

        try:
            va_id = int(va_id) if va_id else False
        except (ValueError, TypeError):
            va_id = False

        va = self.env['seerbit.virtual.account'].browse(va_id) if va_id else self.env['seerbit.virtual.account']
        if not va.exists() and account_number:
            va = self.env['seerbit.virtual.account'].search(
                [('account_number', '=', str(account_number).strip())], order='id desc', limit=1
            )

        acc_num = str(account_number or (va.account_number if va.exists() else '')).strip()

        domain = [
            ('status', '=', 'successful'),
            ('matching_status', 'in', ['unmatched', 'transferred']),
        ]

        if va.exists() and acc_num:
            domain.extend(['|', ('va_id', '=', va.id), ('account_number', '=', acc_num)])
        elif va.exists():
            domain.append(('va_id', '=', va.id))
        elif acc_num:
            domain.append(('account_number', '=', acc_num))
        else:
            return []

        if va.exists() and va.va_type == 'pos_static':
            # For static store virtual accounts, display all unmatched payments from recent days (last 7 days)
            # so legitimate transfers before or during checkout are immediately visible to the cashier
            cutoff_dt = fields.Datetime.now() - datetime.timedelta(days=7)
            domain.append(('payment_date', '>=', fields.Datetime.to_string(cutoff_dt)))

        records = self.search_read(
            domain=domain,
            fields=['id', 'reference', 'payer_name', 'amount', 'payment_date', 'status', 'matching_status', 'transferred_to_config_id', 'is_transferred_in'],
            limit=limit,
            order='payment_date desc'
        )
        from collections import Counter
        unmatched_amounts = Counter(
            round(float(r.get('amount') or 0.0), 2)
            for r in records
            if r.get('matching_status') == 'unmatched'
        )
        for rec in records:
            if isinstance(rec.get('payment_date'), (datetime.datetime, datetime.date)):
                rec['payment_date'] = fields.Datetime.to_string(rec['payment_date'])
            if rec.get('transferred_to_config_id'):
                rec['transferred_to_config_name'] = rec['transferred_to_config_id'][1]
            else:
                rec['transferred_to_config_name'] = False
            amt = round(float(rec.get('amount') or 0.0), 2)
            rec['has_duplicate_amount'] = (unmatched_amounts[amt] > 1) if rec.get('matching_status') == 'unmatched' else False
        return records

    @api.model
    def get_available_tills_for_transfer(self, current_config_id=None):
        """
        Returns all other tills (pos.config) in the company that have
        a SeerBit transfer payment method, including their open session status.
        """
        current_config = self.env['pos.config'].sudo().browse(current_config_id) if current_config_id else None
        company_id = current_config.company_id.id if current_config else self.env.company.id

        configs = self.env['pos.config'].sudo().search([
            ('company_id', '=', company_id),
            ('id', '!=', current_config_id or 0),
        ])

        result = []
        for cfg in configs:
            transfer_methods = cfg.payment_method_ids.filtered(
                lambda m: m.payment_method_type == 'transfer' and m.seerbit_va_id
            )
            if not transfer_methods:
                continue

            open_session = cfg.current_session_id if cfg.current_session_id and cfg.current_session_id.state == 'opened' else None

            result.append({
                'id': cfg.id,
                'name': cfg.name,
                'is_open': bool(open_session),
                'session_id': open_session.id if open_session else False,
                'session_name': open_session.name if open_session else False,
                'payment_methods': [{
                    'id': pm.id,
                    'name': pm.name,
                    'account_number': pm.seerbit_va_account_number,
                    'bank_name': pm.seerbit_va_bank_name,
                    'va_id': pm.seerbit_va_id.id,
                } for pm in transfer_methods]
            })
        return result

    @api.model
    def transfer_payment_to_till(self, transaction_id, target_config_id, target_pm_id=None):
        """
        Transfers an unmatched VA transaction to another till.
        Marks source transaction as 'transferred', creates destination transaction on target VA,
        and broadcasts real-time bus notification to target open session.
        """
        source_tx = self.sudo().browse(transaction_id)
        if not source_tx.exists() or source_tx.matching_status != 'unmatched':
            return {
                'success': False,
                'error': _("This payment is no longer available for transfer (already matched or transferred).")
            }

        target_config = self.env['pos.config'].sudo().browse(target_config_id)
        if not target_config.exists():
            return {'success': False, 'error': _("Target till not found.")}

        if target_pm_id:
            target_pm = self.env['pos.payment.method'].sudo().browse(target_pm_id)
        else:
            target_pm = target_config.payment_method_ids.filtered(
                lambda m: m.payment_method_type == 'transfer' and m.seerbit_va_id
            )[:1]

        if not target_pm or not target_pm.seerbit_va_id:
            return {'success': False, 'error': _("Target till has no SeerBit transfer payment method configured.")}

        target_va = target_pm.seerbit_va_id

        # Unique reference for transferred destination transaction
        dest_ref = f"{source_tx.reference}_TRF_{target_config.id}"
        dest_tx = self.sudo().search([('reference', '=', dest_ref)], limit=1)
        if not dest_tx:
            dest_tx = self.sudo().create({
                'name': self.env['ir.sequence'].next_by_code('seerbit.va.transaction') or dest_ref,
                'va_id': target_va.id,
                'reference': dest_ref,
                'amount': source_tx.amount,
                'payer_name': source_tx.payer_name,
                'payment_date': source_tx.payment_date or fields.Datetime.now(),
                'status': 'successful',
                'matching_status': 'unmatched',
                'is_transferred_in': True,
                'transferred_from_tx_id': source_tx.id,
                'raw_payload': source_tx.raw_payload,
            })

        # Update source transaction
        source_tx.sudo().write({
            'matching_status': 'transferred',
            'transferred_to_config_id': target_config.id,
            'transferred_to_va_id': target_va.id,
            'transferred_to_tx_id': dest_tx.id,
        })

        # Realtime Bus Dispatch to target open session(s)
        open_sessions = self.env['pos.session'].sudo().search([
            ('config_id', '=', target_config.id),
            ('state', '=', 'opened'),
        ])
        for sess in open_sessions:
            sess._broadcast_seerbit_va_event(
                event_type='seerbit_va_payment_received',
                data={
                    'transaction_id': dest_tx.id,
                    'reference': dest_tx.reference,
                    'amount': dest_tx.amount,
                    'payer_name': dest_tx.payer_name,
                    'account_number': target_va.account_number,
                    'is_transferred': True,
                    'from_till': source_tx.company_id.name or "Another Till",
                    'payment_date': fields.Datetime.to_string(dest_tx.payment_date),
                }
            )

        return {
            'success': True,
            'dest_tx_id': dest_tx.id,
            'dest_till_name': target_config.name,
            'dest_ref': dest_ref,
        }

    @api.model
    def match_payment_to_pos_order_atomic(self, transaction_id, pos_order_uuid, cashier_user_id=None, matching_status='manual_matched'):
        """
        Atomic matching with database row-level lock (SELECT ... FOR UPDATE).
        Prevents race conditions when two cashiers attempt to select the same payment simultaneously.
        """
        self.sudo().flush_model(['matching_status', 'pos_order_ref', 'matched_by_user_id', 'matched_at'])
        try:
            self.env.cr.execute(
                "SELECT id, matching_status, amount, reference, payer_name "
                "FROM seerbit_va_transaction WHERE id = %s FOR UPDATE NOWAIT",
                (transaction_id,)
            )
            row = self.env.cr.fetchone()
        except Exception as e:
            _logger.warning("Could not acquire row lock on transaction %s: %s", transaction_id, e)
            return {
                'success': False,
                'error': _("Another cashier is currently processing this payment. Please try another or refresh.")
            }

        if not row:
            return {'success': False, 'error': _("Transaction not found.")}

        tx_id, current_status, amount, reference, payer_name = row
        if current_status != 'unmatched':
            record = self.sudo().browse(tx_id)
            if record.pos_order_ref == pos_order_uuid and current_status in ('auto_matched', 'manual_matched'):
                return {
                    'success': True,
                    'transaction_id': tx_id,
                    'reference': reference,
                    'amount': amount,
                    'payer_name': payer_name,
                    'matched_at': fields.Datetime.to_string(record.matched_at or fields.Datetime.now()),
                }
            return {
                'success': False,
                'error': _("This payment has already been matched to another order (Ref: %s).") % reference
            }

        now = fields.Datetime.now()
        user_id = cashier_user_id or self.env.uid
        record = self.sudo().browse(tx_id)
        record.write({
            'matching_status': matching_status,
            'pos_order_ref': pos_order_uuid,
            'matched_by_user_id': user_id,
            'matched_at': now,
        })
        record.flush_recordset(['matching_status', 'pos_order_ref', 'matched_by_user_id', 'matched_at'])

        return {
            'success': True,
            'transaction_id': tx_id,
            'reference': reference,
            'amount': amount,
            'payer_name': payer_name,
            'matched_at': fields.Datetime.to_string(now),
        }

    def action_view_pos_order(self):
        self.ensure_one()
        if not self.pos_order_id:
            return
        return {
            'name': _('POS Order'),
            'type': 'ir.actions.act_window',
            'res_model': 'pos.order',
            'res_id': self.pos_order_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_view_pos_payment(self):
        self.ensure_one()
        if not self.pos_payment_id:
            return
        return {
            'name': _('POS Payment'),
            'type': 'ir.actions.act_window',
            'res_model': 'pos.payment',
            'res_id': self.pos_payment_id.id,
            'view_mode': 'form',
            'target': 'current',
        }


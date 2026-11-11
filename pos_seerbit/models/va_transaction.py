# -*- coding: utf-8 -*-
import datetime
import json
import logging
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
        ('cancelled', 'Cancelled')
    ], default='unmatched', required=True, index=True)

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
    def register_inbound_transaction(self, va, reference, amount, payer_name, raw_payload=None, payment_date=None):
        """
        Idempotent creation or retrieval of inbound VA transaction.
        Triggers POS auto-matching engine if va_type is pos_static or pos_dynamic.
        """
        existing = self.search([('reference', '=', reference)], limit=1)
        if existing:
            if payer_name and payer_name != 'Bank Customer' and existing.payer_name == 'Bank Customer':
                existing.write({'payer_name': payer_name})
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
        transaction = self.create(vals)

        if va.va_type in ('pos_static', 'pos_dynamic'):
            # Check if an existing pos.payment or pos.order already references this transaction
            payment = self.env['pos.payment'].search([
                '|',
                ('transaction_id', '=', reference),
                ('seerbit_transaction_ref', '=', reference)
            ], limit=1)
            if payment and payment.pos_order_id:
                order = payment.pos_order_id
                transaction.write({
                    'matching_status': 'auto_matched' if va.va_type == 'pos_dynamic' else 'manual_matched',
                    'pos_order_id': order.id,
                    'pos_order_ref': order.pos_reference or order.name,
                    'pos_payment_id': payment.id,
                    'pos_session_id': order.session_id.id,
                    'matched_by_user_id': order.user_id.id or self.env.uid,
                    'matched_at': fields.Datetime.now(),
                })
                payment.write({'seerbit_va_transaction_id': transaction.id})
                if not order.seerbit_va_transaction_id:
                    order.write({
                        'seerbit_va_transaction_id': transaction.id,
                        'seerbit_transaction_ref': reference,
                        'seerbit_payer_name': payer_name or 'Bank Customer',
                        'seerbit_amount_received': float(amount or 0.0),
                    })
            else:
                # Attempt auto-match against open orders
                self.env['pos.order']._attempt_va_auto_match(transaction)

        return transaction

    @api.model
    def get_unmatched_payments_for_pos(self, va_id, order_created_at_str=None, limit=20):
        """
        RPC method called by POS Cashier to view available incoming payments.
        Lists unmatched payments for this virtual account.
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
        if not va.exists():
            return []

        domain = [
            ('va_id', '=', va.id),
            ('status', '=', 'successful'),
            ('matching_status', '=', 'unmatched'),
        ]

        if va.va_type == 'pos_dynamic' and order_created_at_str:
            try:
                company = va.company_id
                window_mins = company.seerbit_pos_va_match_window_minutes or 30
                clean_str = order_created_at_str.replace('Z', '').split('.')[0]
                if 'T' in clean_str:
                    order_dt = datetime.datetime.fromisoformat(clean_str)
                else:
                    order_dt = fields.Datetime.from_string(clean_str)
                buffer_dt = order_dt - datetime.timedelta(minutes=window_mins)
                domain.append(('payment_date', '>=', fields.Datetime.to_string(buffer_dt)))
            except Exception as e:
                _logger.warning("Failed to parse order_created_at_str %s: %s", order_created_at_str, e)
        elif va.va_type == 'pos_static':
            # For static store virtual accounts, display all unmatched payments from recent days (last 48 hours)
            # so legitimate transfers before or during checkout are immediately visible to the cashier
            cutoff_dt = fields.Datetime.now() - datetime.timedelta(hours=48)
            domain.append(('payment_date', '>=', fields.Datetime.to_string(cutoff_dt)))

        records = self.search_read(
            domain=domain,
            fields=['id', 'reference', 'payer_name', 'amount', 'payment_date', 'status'],
            limit=limit,
            order='payment_date desc'
        )
        for rec in records:
            if isinstance(rec.get('payment_date'), (datetime.datetime, datetime.date)):
                rec['payment_date'] = fields.Datetime.to_string(rec['payment_date'])
        return records

    @api.model
    def match_payment_to_pos_order_atomic(self, transaction_id, pos_order_uuid, cashier_user_id=None):
        """
        Atomic matching with database row-level lock (SELECT ... FOR UPDATE).
        Prevents race conditions when two cashiers attempt to select the same payment simultaneously.
        """
        self.flush_model(['matching_status', 'pos_order_ref', 'matched_by_user_id', 'matched_at'])
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

        tx_id, matching_status, amount, reference, payer_name = row
        if matching_status != 'unmatched':
            return {
                'success': False,
                'error': _("This payment has already been matched to another order (Ref: %s).") % reference
            }

        now = fields.Datetime.now()
        user_id = cashier_user_id or self.env.uid
        record = self.browse(tx_id)
        record.write({
            'matching_status': 'manual_matched',
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


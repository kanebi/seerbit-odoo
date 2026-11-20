# -*- coding: utf-8 -*-
import json
from unittest.mock import patch

from odoo import fields
from odoo.tests.common import HttpCase, tagged

from .common import SeerbitCommon


@tagged('post_install', '-at_install', 'pos_seerbit')
class TestPosVaFeatures(SeerbitCommon, HttpCase):

    def setUp(self):
        super().setUp()
        # Create a static VA
        self.static_va = self.env['seerbit.virtual.account'].create({
            'name': 'Branch Static VA Tests',
            'company_id': self.company_a.id,
            'va_type': 'pos_static',
            'account_number': '1057329833',
            'bank_name': 'Moniepoint MFB',
            'reference': 'VA_STATIC_FEAT_01',
        })

        # Create a dynamic VA
        self.dynamic_va = self.env['seerbit.virtual.account'].create({
            'name': 'Dynamic VA Tests',
            'company_id': self.company_a.id,
            'va_type': 'pos_dynamic',
            'account_number': '6213873086',
            'bank_name': 'Wema Bank',
            'reference': 'VA_DYNAMIC_FEAT_01',
        })

        # Payment method for static transfer
        self.pm_static = self.env['pos.payment.method'].create({
            'name': 'Seerbit Transfer Static',
            'company_id': self.company_a.id,
            'payment_method_type': 'transfer',
            'seerbit_transfer_type': 'static',
            'seerbit_va_id': self.static_va.id,
            'journal_id': self.journal_a.id,
        })

        # Payment method for dynamic transfer
        self.pm_dynamic = self.env['pos.payment.method'].create({
            'name': 'Seerbit Transfer Dynamic',
            'company_id': self.company_a.id,
            'payment_method_type': 'transfer',
            'seerbit_transfer_type': 'dynamic',
            'journal_id': self.journal_a.id,
        })

        # POS config and session
        self.pos_config = self.env['pos.config'].create({
            'name': 'Features POS Config',
            'company_id': self.company_a.id,
            'payment_method_ids': [(4, self.pm_static.id), (4, self.pm_dynamic.id)],
        })
        self.session = self.env['pos.session'].create({
            'config_id': self.pos_config.id,
            'user_id': self.env.uid,
        })
        self.session.write({'state': 'opened'})

    def _create_pos_order(self, pm, amount=50.0, ref='TEST_REF_001'):
        order = self.env['pos.order'].create({
            'session_id': self.session.id,
            'company_id': self.company_a.id,
            'date_order': fields.Datetime.now(),
            'pos_reference': f'Order {ref}',
            'lines': [(0, 0, {
                'product_id': self.product.id,
                'qty': 1,
                'price_unit': amount,
                'price_subtotal': amount,
                'price_subtotal_incl': amount,
            })],
            'amount_total': amount,
            'amount_tax': 0.0,
            'amount_paid': amount,
            'amount_return': 0.0,
        })
        payment = self.env['pos.payment'].create({
            'pos_order_id': order.id,
            'payment_method_id': pm.id,
            'amount': amount,
            'transaction_id': ref,
            'seerbit_transaction_ref': ref,
        })
        return order, payment

    def test_smart_buttons_action_view_pos_order_and_payment(self):
        """Test action_view_pos_order and action_view_pos_payment return window actions."""
        order, payment = self._create_pos_order(self.pm_static, 50.0, 'SBT_SMART_01')

        tx = self.env['seerbit.va.transaction'].create({
            'name': 'TX_SMART_01',
            'va_id': self.static_va.id,
            'reference': 'SBT_SMART_01',
            'amount': 50.0,
            'payer_name': 'Smart Button User',
            'status': 'successful',
        })

        # When no order or payment linked, methods return None
        self.assertFalse(tx.action_view_pos_order())
        self.assertFalse(tx.action_view_pos_payment())

        # Link order and payment
        tx.write({
            'pos_order_id': order.id,
            'pos_payment_id': payment.id,
        })

        # Order action
        action_order = tx.action_view_pos_order()
        self.assertEqual(action_order['type'], 'ir.actions.act_window')
        self.assertEqual(action_order['res_model'], 'pos.order')
        self.assertEqual(action_order['res_id'], order.id)
        self.assertEqual(action_order['view_mode'], 'form')

        # Payment action
        action_payment = tx.action_view_pos_payment()
        self.assertEqual(action_payment['type'], 'ir.actions.act_window')
        self.assertEqual(action_payment['res_model'], 'pos.payment')
        self.assertEqual(action_payment['res_id'], payment.id)
        self.assertEqual(action_payment['view_mode'], 'form')

    def test_dynamic_va_auto_matched_on_inbound_and_order_save(self):
        """Dynamic VA transfers must be marked as auto_matched and fully populate settlement details."""
        order, payment = self._create_pos_order(self.pm_dynamic, 50.0, 'SBT_DYN_MATCH_01')

        # Register inbound transaction for dynamic VA
        tx = self.env['seerbit.va.transaction'].register_inbound_transaction(
            va=self.dynamic_va,
            reference='SBT_DYN_MATCH_01',
            amount=50.0,
            payer_name='Dynamic Customer',
        )

        # Must be auto_matched because it is dynamic VA
        self.assertEqual(tx.matching_status, 'auto_matched')
        self.assertEqual(tx.pos_order_id, order)
        self.assertEqual(tx.pos_payment_id, payment)
        self.assertEqual(tx.pos_session_id, self.session)
        self.assertEqual(tx.matched_by_user_id, order.user_id)
        self.assertTrue(tx.matched_at)

        # Order and payment must be updated with Seerbit references
        self.assertEqual(order.seerbit_va_transaction_id, tx)
        self.assertEqual(order.seerbit_transaction_ref, 'SBT_DYN_MATCH_01')
        self.assertEqual(order.seerbit_payer_name, 'Dynamic Customer')
        self.assertEqual(order.seerbit_amount_received, 50.0)

        self.assertEqual(payment.seerbit_va_transaction_id, tx)
        self.assertEqual(payment.seerbit_transaction_ref, 'SBT_DYN_MATCH_01')

        # When order completes saved order processing, auto_matched is preserved
        order._process_saved_order(draft=False)
        self.assertEqual(tx.matching_status, 'auto_matched')

    def test_static_va_manual_matching_atomic(self):
        """Cashier manual selection sets manual_matched and locks against double-matching."""
        order, payment = self._create_pos_order(self.pm_static, 50.0, 'SBT_STATIC_MANUAL_01')

        tx = self.env['seerbit.va.transaction'].create({
            'name': 'TX_STATIC_MANUAL',
            'va_id': self.static_va.id,
            'reference': 'SBT_STATIC_MANUAL_01',
            'amount': 50.0,
            'payer_name': 'Manual Payer',
            'status': 'successful',
            'matching_status': 'unmatched',
        })

        # Cashier manually selects the payment in the POS modal
        res = self.env['seerbit.va.transaction'].match_payment_to_pos_order_atomic(
            transaction_id=tx.id,
            pos_order_uuid=order.pos_reference,
            cashier_user_id=self.env.uid,
        )
        self.assertTrue(res['success'])
        self.assertEqual(tx.matching_status, 'manual_matched')
        self.assertEqual(tx.pos_order_ref, order.pos_reference)
        self.assertEqual(tx.matched_by_user_id.id, self.env.uid)

        # Second attempt by another cashier must fail (atomic concurrency lock)
        res_duplicate = self.env['seerbit.va.transaction'].match_payment_to_pos_order_atomic(
            transaction_id=tx.id,
            pos_order_uuid='ANOTHER_ORDER',
        )
        self.assertFalse(res_duplicate['success'])
        self.assertIn('already been matched', res_duplicate['error'])

        # Order validation keeps manual_matched
        order._process_saved_order(draft=False)
        self.assertEqual(tx.matching_status, 'manual_matched')
        self.assertEqual(tx.pos_order_id, order)
        self.assertEqual(tx.pos_payment_id, payment)

    def test_static_va_auto_matched_when_unmatched_at_save(self):
        """If incoming payment on static VA was auto-matched in POS frontend without atomic pick, it becomes auto_matched."""
        order, payment = self._create_pos_order(self.pm_static, 50.0, 'SBT_STATIC_AUTO_01')

        tx = self.env['seerbit.va.transaction'].create({
            'name': 'TX_STATIC_AUTO',
            'va_id': self.static_va.id,
            'reference': 'SBT_STATIC_AUTO_01',
            'amount': 50.0,
            'payer_name': 'Auto Matched Payer',
            'status': 'successful',
            'matching_status': 'unmatched',
        })

        # Payment line has the reference from the bus event
        payment.write({
            'seerbit_va_transaction_id': tx.id,
            'seerbit_transaction_ref': tx.reference,
        })

        # Save order
        order._process_saved_order(draft=False)
        # Because it was unmatched prior to order completion, it becomes auto_matched
        self.assertEqual(tx.matching_status, 'auto_matched')
        self.assertEqual(tx.pos_order_id, order)
        self.assertEqual(tx.pos_payment_id, payment)
        self.assertTrue(tx.matched_at)
        self.assertEqual(tx.matched_by_user_id, order.user_id)

    def test_inbound_transaction_idempotency_and_name_enrichment(self):
        """register_inbound_transaction is idempotent and updates default 'Bank Customer' name."""
        tx1 = self.env['seerbit.va.transaction'].register_inbound_transaction(
            va=self.static_va,
            reference='SBT_IDEM_01',
            amount=100.0,
            payer_name='Bank Customer',
        )
        self.assertEqual(tx1.payer_name, 'Bank Customer')

        # Re-register with same reference but enriched real customer name
        tx2 = self.env['seerbit.va.transaction'].register_inbound_transaction(
            va=self.static_va,
            reference='SBT_IDEM_01',
            amount=100.0,
            payer_name='Emmanuel Kanebi',
        )
        self.assertEqual(tx1.id, tx2.id)
        self.assertEqual(tx1.payer_name, 'Emmanuel Kanebi')

    def test_action_seerbit_va_transaction_default_filter_is_all(self):
        """Action for Inbound Transactions must not have search_default_unmatched active."""
        action = self.env.ref('pos_seerbit.action_seerbit_va_transaction')
        self.assertTrue(action)
        # Context should be empty or not specify search_default_unmatched
        context = action.context or '{}'
        if isinstance(context, str):
            import ast
            try:
                context = ast.literal_eval(context)
            except Exception:
                context = {}
        self.assertNotIn('search_default_unmatched', context)

    def test_webhook_ack_reference_and_status(self):
        """Webhook should return X-Expected-Ack-Reference in ackReference and status processed."""
        payload = {
            'notificationItems': [{
                'notificationRequestItem': {
                    'eventType': 'transaction',
                    'data': {
                        'creditAccountNumber': '1057329833',
                        'amount': 50.0,
                        'reference': 'WH_ACK_REF_TEST_01',
                        'customerName': 'Ack Test Customer',
                        'gatewayCode': '00',
                        'publicKey': 'SB_PUB_TEST_A',
                    }
                }
            }]
        }
        headers = {
            'Content-Type': 'application/json',
            'X-Expected-Ack-Reference': 'EXPECTED_ACK_12345',
        }
        res = self.url_open('/api/seerbit/webhook', data=json.dumps(payload), headers=headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data.get('ackReference'), 'EXPECTED_ACK_12345')
        self.assertEqual(data.get('status'), 'processed')

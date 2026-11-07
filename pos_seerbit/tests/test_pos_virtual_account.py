# -*- coding: utf-8 -*-
from unittest.mock import MagicMock, patch
import datetime
from psycopg2 import IntegrityError

from odoo import fields
from odoo.tests.common import TransactionCase, tagged
from odoo.tools import mute_logger

from .common import SeerbitCommon


@tagged('post_install', '-at_install', 'pos_seerbit')
class TestPosVirtualAccount(SeerbitCommon):

    def test_va_partner_optional_and_defaults_to_company(self):
        va = self.env['seerbit.virtual.account'].create({
            'name': 'Branch Static VA',
            'company_id': self.company_a.id,
            'va_type': 'pos_static',
            'account_number': '1112229999',
            'bank_name': 'Providus Bank',
            'reference': 'VA_STATIC_TEST_1',
        })
        self.assertTrue(va)
        self.assertEqual(va.va_type, 'pos_static')
        self.assertEqual(va.partner_id, self.company_a.partner_id)

    def test_va_transaction_reference_unique_constraint(self):
        va = self.env['seerbit.virtual.account'].create({
            'name': 'Test VA',
            'company_id': self.company_a.id,
            'va_type': 'pos_static',
            'account_number': '2223334444',
            'reference': 'VA_UNIQUE_TEST',
        })
        self.env['seerbit.va.transaction'].create({
            'name': 'TX01',
            'va_id': va.id,
            'reference': 'REF_UNIQUE_001',
            'amount': 5000.0,
        })
        with mute_logger('odoo.sql_db'):
            with self.assertRaises(IntegrityError):
                with self.cr.savepoint():
                    self.env['seerbit.va.transaction'].create({
                        'name': 'TX02',
                        'va_id': va.id,
                        'reference': 'REF_UNIQUE_001',
                        'amount': 5000.0,
                    })

    def test_get_unmatched_payments_for_pos(self):
        va = self.env['seerbit.virtual.account'].create({
            'name': 'POS Till VA',
            'company_id': self.company_a.id,
            'va_type': 'pos_static',
            'account_number': '7778889999',
            'reference': 'VA_RPC_TEST',
        })
        # 1 unmatched, 1 already matched
        tx1 = self.env['seerbit.va.transaction'].create({
            'name': 'TX_UNMATCHED',
            'va_id': va.id,
            'reference': 'REF_RPC_001',
            'amount': 15000.0,
            'payer_name': 'Alice Smith',
            'status': 'successful',
            'matching_status': 'unmatched',
            'payment_date': fields.Datetime.now(),
        })
        tx2 = self.env['seerbit.va.transaction'].create({
            'name': 'TX_MATCHED',
            'va_id': va.id,
            'reference': 'REF_RPC_002',
            'amount': 20000.0,
            'payer_name': 'Bob Jones',
            'status': 'successful',
            'matching_status': 'manual_matched',
            'payment_date': fields.Datetime.now(),
        })

        results = self.env['seerbit.va.transaction'].get_unmatched_payments_for_pos(
            va_id=va.id,
            order_created_at_str=fields.Datetime.to_string(fields.Datetime.now()),
            limit=10,
        )
        refs = [r['reference'] for r in results]
        self.assertIn('REF_RPC_001', refs)
        self.assertNotIn('REF_RPC_002', refs)

    def test_auto_match_ambiguity_refuses_guess(self):
        # Setup payment method with transfer
        va = self.env['seerbit.virtual.account'].create({
            'name': 'POS Auto VA',
            'company_id': self.company_a.id,
            'va_type': 'pos_static',
            'account_number': '4445556666',
            'reference': 'VA_AUTO_AMB',
        })
        pm = self.env['pos.payment.method'].create({
            'name': 'SeerBit Bank Transfer',
            'company_id': self.company_a.id,
            'payment_method_type': 'transfer',
            'seerbit_transfer_type': 'static',
            'seerbit_va_id': va.id,
            'journal_id': self.journal_a.id,
        })
        # Setup POS session
        pos_config = self.env['pos.config'].create({
            'name': 'Test POS Config',
            'company_id': self.company_a.id,
            'payment_method_ids': [(4, pm.id)],
        })
        session = self.env['pos.session'].create({
            'config_id': pos_config.id,
            'user_id': self.env.uid,
        })
        session.write({'state': 'opened'})

        # Create TWO draft orders with identical amount 50000.0
        order1 = self.env['pos.order'].create({
            'session_id': session.id,
            'company_id': self.company_a.id,
            'date_order': fields.Datetime.now(),
            'pos_reference': 'Order 0001',
            'amount_total': 50000.0,
            'amount_tax': 0.0,
            'amount_paid': 0.0,
            'amount_return': 0.0,
            'state': 'draft',
        })
        order2 = self.env['pos.order'].create({
            'session_id': session.id,
            'company_id': self.company_a.id,
            'date_order': fields.Datetime.now(),
            'pos_reference': 'Order 0002',
            'amount_total': 50000.0,
            'amount_tax': 0.0,
            'amount_paid': 0.0,
            'amount_return': 0.0,
            'state': 'draft',
        })

        # Register inbound transaction of 50000.0
        with patch.object(type(session), '_broadcast_seerbit_va_event') as mock_broadcast:
            tx = self.env['seerbit.va.transaction'].register_inbound_transaction(
                va=va,
                reference='SB_AMBIGUOUS_50K',
                amount=50000.0,
                payer_name='Ambiguous Payer',
            )
            # Must remain UNMATCHED due to ambiguity safety rule!
            self.assertEqual(tx.matching_status, 'unmatched')
            self.assertFalse(tx.pos_order_id)
            # Should have broadcast seerbit_va_payment_received (not matched)
            mock_broadcast.assert_called_with(
                event_type='seerbit_va_payment_received',
                data={
                    'transaction_id': tx.id,
                    'reference': tx.reference,
                    'amount': tx.amount,
                    'payer_name': tx.payer_name,
                    'account_number': tx.account_number,
                    'payment_date': fields.Datetime.to_string(tx.payment_date),
                }
            )

    def test_auto_match_single_candidate_succeeds(self):
        va = self.env['seerbit.virtual.account'].create({
            'name': 'POS Auto VA 2',
            'company_id': self.company_a.id,
            'va_type': 'pos_static',
            'account_number': '3334445555',
            'reference': 'VA_AUTO_SINGLE',
        })
        pm = self.env['pos.payment.method'].create({
            'name': 'SeerBit Bank Transfer 2',
            'company_id': self.company_a.id,
            'payment_method_type': 'transfer',
            'seerbit_transfer_type': 'static',
            'seerbit_va_id': va.id,
            'journal_id': self.journal_a.id,
        })
        pos_config = self.env['pos.config'].create({
            'name': 'Test POS Config 2',
            'company_id': self.company_a.id,
            'payment_method_ids': [(4, pm.id)],
        })
        session = self.env['pos.session'].create({
            'config_id': pos_config.id,
            'user_id': self.env.uid,
        })
        session.write({'state': 'opened'})

        # Exactly ONE draft order with amount 75000.0
        order = self.env['pos.order'].create({
            'session_id': session.id,
            'company_id': self.company_a.id,
            'date_order': fields.Datetime.now(),
            'pos_reference': 'Order 0003',
            'amount_total': 75000.0,
            'amount_tax': 0.0,
            'amount_paid': 0.0,
            'amount_return': 0.0,
            'state': 'draft',
        })

        with patch.object(type(session), '_broadcast_seerbit_va_event') as mock_broadcast:
            tx = self.env['seerbit.va.transaction'].register_inbound_transaction(
                va=va,
                reference='SB_SINGLE_75K',
                amount=75000.0,
                payer_name='Single Match Payer',
            )
            # Successfully auto-matched!
            self.assertEqual(tx.matching_status, 'auto_matched')
            self.assertEqual(tx.pos_order_id, order)
            mock_broadcast.assert_called_with(
                event_type='seerbit_va_payment_matched',
                data={
                    'transaction_id': tx.id,
                    'reference': tx.reference,
                    'amount': tx.amount,
                    'payer_name': tx.payer_name,
                    'order_id': order.id,
                    'order_ref': order.pos_reference or order.name,
                }
            )

    def test_auto_generate_va_on_payment_method_save(self):
        mock_response = {
            'code': 'S20',
            'payments': {
                'walletName': 'Auto Store - Transfer',
                'wallet': '5544332211',
                'bankName': 'First Bank',
                'accountNumber': '5544332211',
                'reference': 'POS_AUTO_SAVE_1',
            }
        }
        with patch('odoo.addons.pos_seerbit.services.seerbit_api.SeerbitAPI.create_virtual_account', return_value=mock_response):
            pm = self.env['pos.payment.method'].create({
                'name': 'Auto VA Transfer Method',
                'company_id': self.company_a.id,
                'payment_method_type': 'transfer',
                'seerbit_transfer_type': 'static',
                'journal_id': self.journal_a.id,
            })
            # Verified: saving the payment method automatically ran generate VA!
            self.assertTrue(pm.seerbit_va_id)
            self.assertEqual(pm.seerbit_va_account_number, '5544332211')
            self.assertEqual(pm.seerbit_va_bank_name, 'First Bank')

    def test_action_generate_static_va_from_pos_payment_method(self):
        mock_response = {
            'code': 'S20',
            'payments': {
                'walletName': 'Test Store - Transfer',
                'wallet': '9988776655',
                'bankName': 'Wema Bank',
                'accountNumber': '9988776655',
                'reference': 'POS_STATIC_MOCK_1',
            }
        }
        # Create without public key so auto-generation on create is skipped
        company_without_keys = self.env['res.company'].create({'name': 'No Keys Company'})
        pm = self.env['pos.payment.method'].create({
            'name': 'Seerbit Transfer Test Method',
            'company_id': company_without_keys.id,
            'payment_method_type': 'transfer',
            'seerbit_transfer_type': 'static',
        })
        self.assertFalse(pm.seerbit_va_id)

        # Now configure company and click button
        company_without_keys.write({
            'seerbit_public_key': 'KEY_TEST_MOCK',
            'seerbit_secret_key': 'SEC_TEST_MOCK',
        })
        company_without_keys.partner_id.write({'email': 'nokeys@example.com'})

        with patch('odoo.addons.pos_seerbit.services.seerbit_api.SeerbitAPI.create_virtual_account', return_value=mock_response):
            res = pm.action_generate_static_va()
            self.assertTrue(pm.seerbit_va_id)
            self.assertEqual(pm.seerbit_va_id.va_type, 'pos_static')
            self.assertEqual(pm.seerbit_va_account_number, '9988776655')
            self.assertEqual(pm.seerbit_va_bank_name, 'Wema Bank')
            self.assertEqual(res.get('type'), 'ir.actions.client')
            next_action = res.get('params', {}).get('next', {})
            self.assertEqual(next_action.get('type'), 'ir.actions.act_window')
            self.assertEqual(next_action.get('res_id'), pm.id)

    def test_action_generate_va_on_pos_static_virtual_account(self):
        va = self.env['seerbit.virtual.account'].create({
            'name': 'Direct Static VA',
            'company_id': self.company_a.id,
            'va_type': 'pos_static',
        })
        self.assertFalse(va.account_number)

        mock_response = {
            'code': 'S20',
            'payments': {
                'walletName': 'Direct Static VA',
                'wallet': '1122334455',
                'bankName': 'Zenith Bank',
                'accountNumber': '1122334455',
                'reference': 'POS_STATIC_MOCK_2',
            }
        }
        with patch('odoo.addons.pos_seerbit.services.seerbit_api.SeerbitAPI.create_virtual_account', return_value=mock_response):
            res = va.action_generate_va()
            self.assertEqual(va.account_number, '1122334455')
            self.assertEqual(va.bank_name, 'Zenith Bank')
            self.assertEqual(res.get('type'), 'ir.actions.client')
            next_action = res.get('params', {}).get('next', {})
            self.assertEqual(next_action.get('type'), 'ir.actions.act_window')
            self.assertEqual(next_action.get('res_id'), va.id)

    def test_dynamic_transfer_does_not_generate_va_on_save_or_create(self):
        # Even with valid API credentials on company, dynamic methods must NEVER generate VA on create/write
        with patch('odoo.addons.pos_seerbit.services.seerbit_api.SeerbitAPI.create_virtual_account') as mock_create:
            pm = self.env['pos.payment.method'].create({
                'name': 'Dynamic Transfer Till',
                'company_id': self.company_a.id,
                'payment_method_type': 'transfer',
                'seerbit_transfer_type': 'dynamic',
                'journal_id': self.journal_a.id,
            })
            mock_create.assert_not_called()
            self.assertFalse(pm.seerbit_va_id)

            # Saving/writing again should not trigger generation either
            pm.write({'name': 'Dynamic Transfer Till Updated'})
            mock_create.assert_not_called()
            self.assertFalse(pm.seerbit_va_id)

            # Generating static VA on dynamic method must be forbidden
            from odoo.exceptions import UserError
            with self.assertRaises(UserError):
                pm.action_generate_static_va()

    def test_switching_to_dynamic_clears_static_va(self):
        mock_response = {
            'code': 'S20',
            'payments': {
                'walletName': 'Static Store',
                'bankName': 'Providus Bank',
                'accountNumber': '3334445556',
                'reference': 'POS_STATIC_SW_1',
            }
        }
        with patch('odoo.addons.pos_seerbit.services.seerbit_api.SeerbitAPI.create_virtual_account', return_value=mock_response):
            pm = self.env['pos.payment.method'].create({
                'name': 'Switchable Method',
                'company_id': self.company_a.id,
                'payment_method_type': 'transfer',
                'seerbit_transfer_type': 'static',
                'journal_id': self.journal_a.id,
            })
            self.assertTrue(pm.seerbit_va_id)

            # Switch to dynamic -> seerbit_va_id must be cleared
            pm.write({'seerbit_transfer_type': 'dynamic'})
            self.assertFalse(pm.seerbit_va_id)

    def test_create_dynamic_va_for_pos_during_payment(self):
        pm = self.env['pos.payment.method'].create({
            'name': 'Dynamic Method POS',
            'company_id': self.company_a.id,
            'payment_method_type': 'transfer',
            'seerbit_transfer_type': 'dynamic',
            'journal_id': self.journal_a.id,
        })
        self.assertFalse(pm.seerbit_va_id)

        mock_dynamic_response = {
            'status': 'SUCCESS',
            'data': {
                'payments': {
                    'walletName': 'Order POS_001',
                    'bankName': 'Wema Bank',
                    'accountNumber': '8877665544',
                    'reference': 'POS_DYN_POS_001_1234',
                }
            }
        }
        with patch('odoo.addons.pos_seerbit.services.seerbit_api.SeerbitAPI.create_dynamic_virtual_account', return_value=mock_dynamic_response['data']):
            res = pm.create_dynamic_va_for_pos(
                order_ref="POS_001",
                amount=7500.0,
                customer_name="Jane Doe",
                customer_email="jane@example.com"
            )
            self.assertEqual(res['account_number'], '8877665544')
            self.assertEqual(res['bank_name'], 'Wema Bank')
            self.assertEqual(res['amount'], 7500.0)

            # Ensure the dynamic VA was saved in seerbit.virtual.account with va_type='pos_dynamic'
            va = self.env['seerbit.virtual.account'].browse(res['va_id'])
            self.assertTrue(va.exists())
            self.assertEqual(va.va_type, 'pos_dynamic')
            self.assertEqual(va.account_number, '8877665544')

    def test_create_dynamic_va_rejects_amount_under_one(self):
        pm = self.env['pos.payment.method'].create({
            'name': 'Dynamic Method POS 2',
            'company_id': self.company_a.id,
            'payment_method_type': 'transfer',
            'seerbit_transfer_type': 'dynamic',
            'journal_id': self.journal_a.id,
        })
        from odoo.exceptions import UserError
        with self.assertRaises(UserError):
            pm.create_dynamic_va_for_pos("POS_LOW", amount=0.50)
        with self.assertRaises(UserError):
            pm.create_dynamic_va_for_pos("POS_LOW", amount=1.00)

    def test_backend_gating_blocks_unmatched_transfer_order(self):
        from odoo.exceptions import UserError
        va = self.env['seerbit.virtual.account'].create({
            'name': 'Branch Static VA',
            'company_id': self.company_a.id,
            'va_type': 'pos_static',
            'account_number': '1112223334',
            'bank_name': 'Providus Bank',
            'reference': 'VA_GATE_TEST',
        })
        pm = self.env['pos.payment.method'].create({
            'name': 'Transfer PM Gate',
            'company_id': self.company_a.id,
            'payment_method_type': 'transfer',
            'seerbit_transfer_type': 'static',
            'seerbit_va_id': va.id,
            'journal_id': self.journal_a.id,
        })
        # Open session and order
        pos_config = self.env['pos.config'].create({
            'name': 'Gate Test Config',
            'company_id': self.company_a.id,
            'payment_method_ids': [(4, pm.id)],
        })
        session = self.env['pos.session'].create({
            'config_id': pos_config.id,
            'user_id': self.env.uid,
        })
        order = self.env['pos.order'].create({
            'session_id': session.id,
            'company_id': self.company_a.id,
            'lines': [(0, 0, {
                'product_id': self.product.id,
                'qty': 1,
                'price_unit': 500.0,
                'price_subtotal': 500.0,
                'price_subtotal_incl': 500.0,
            })],
            'amount_total': 500.0,
            'amount_tax': 0.0,
            'amount_paid': 500.0,
            'amount_return': 0.0,
        })
        # Add payment line WITHOUT matched transaction
        payment = self.env['pos.payment'].create({
            'pos_order_id': order.id,
            'payment_method_id': pm.id,
            'amount': 500.0,
        })
        # Attempting to validate or mark paid must raise UserError
        with self.assertRaises(UserError):
            order.action_pos_order_paid()
        with self.assertRaises(UserError):
            order._process_saved_order(draft=False)

        # Now link matched transaction
        tx = self.env['seerbit.va.transaction'].create({
            'name': 'TX_GATE',
            'va_id': va.id,
            'reference': 'REF_GATE_MATCH',
            'amount': 500.0,
            'status': 'successful',
            'matching_status': 'manual_matched',
        })
        payment.write({
            'seerbit_va_transaction_id': tx.id,
            'seerbit_transaction_ref': tx.reference,
        })
        # Now validation succeeds!
        order.action_pos_order_paid()
        self.assertEqual(order.state, 'paid')


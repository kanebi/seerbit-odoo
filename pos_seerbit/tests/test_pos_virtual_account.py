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

        # Also query using account_number fallback without va_id
        results_by_acc = self.env['seerbit.va.transaction'].get_unmatched_payments_for_pos(
            va_id=False,
            account_number='7778889999',
            limit=10,
        )
        refs_acc = [r['reference'] for r in results_by_acc]
        self.assertIn('REF_RPC_001', refs_acc)

    def test_dynamic_va_overpayment_displays_in_unmatched(self):
        dyn_va = self.env['seerbit.virtual.account'].create({
            'name': 'Dynamic VA Overpay',
            'company_id': self.company_a.id,
            'va_type': 'pos_dynamic',
            'account_number': '1070655184',
            'reference': 'POS_DYN_OVERPAY_1',
        })
        # Customer pays 25.0 into dynamic account for 21.50 order
        tx = self.env['seerbit.va.transaction'].create({
            'name': 'TX_OVERPAY',
            'va_id': dyn_va.id,
            'reference': 'REF_OVERPAY_25',
            'amount': 25.0,
            'payer_name': 'APOLOSI COMFORT',
            'status': 'successful',
            'matching_status': 'unmatched',
            'payment_date': fields.Datetime.now(),
        })

        # Query should return the 25.0 payment even if order_created_at is given
        payments = self.env['seerbit.va.transaction'].get_unmatched_payments_for_pos(
            va_id=dyn_va.id,
            order_created_at_str=fields.Datetime.to_string(fields.Datetime.now()),
            account_number='1070655184',
        )
        refs = [p['reference'] for p in payments]
        self.assertIn('REF_OVERPAY_25', refs)
        self.assertEqual(payments[0]['amount'], 25.0)

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

    def test_auto_match_refuses_duplicate_same_amount_payments(self):
        """
        Verify that if multiple payments of the identical amount arrive or are spooled,
        auto-match is aborted and cashiers must manually match the payment.
        """
        va = self.env['seerbit.virtual.account'].create({
            'name': 'POS Dup Test VA',
            'company_id': self.company_a.id,
            'va_type': 'pos_static',
            'account_number': '7778889999',
            'reference': 'VA_DUP_TEST',
        })
        pm = self.env['pos.payment.method'].create({
            'name': 'SeerBit Bank Transfer Dup',
            'company_id': self.company_a.id,
            'payment_method_type': 'transfer',
            'seerbit_transfer_type': 'static',
            'seerbit_va_id': va.id,
            'journal_id': self.journal_a.id,
        })
        pos_config = self.env['pos.config'].create({
            'name': 'POS Config Dup',
            'company_id': self.company_a.id,
            'payment_method_ids': [(4, pm.id)],
        })
        session = self.env['pos.session'].create({
            'config_id': pos_config.id,
            'user_id': self.env.uid,
        })
        session.write({'state': 'opened'})

        # ONE draft order for 30000.0
        order = self.env['pos.order'].create({
            'session_id': session.id,
            'company_id': self.company_a.id,
            'date_order': fields.Datetime.now(),
            'pos_reference': 'Order Dup 1',
            'amount_total': 30000.0,
            'amount_tax': 0.0,
            'amount_paid': 0.0,
            'amount_return': 0.0,
            'state': 'draft',
        })

        # 1. First transaction of 30000 arrives via webhook/spooling
        tx1 = self.env['seerbit.va.transaction'].create({
            'name': 'TX_DUP_001',
            'va_id': va.id,
            'reference': 'REF_DUP_001',
            'amount': 30000.0,
            'payer_name': 'First Customer',
            'matching_status': 'unmatched',
            'status': 'successful',
        })

        # 2. Second transaction of 30000 arrives via register_inbound_transaction
        with patch.object(type(session), '_broadcast_seerbit_va_event') as mock_broadcast:
            tx2 = self.env['seerbit.va.transaction'].register_inbound_transaction(
                va=va,
                reference='REF_DUP_002',
                amount=30000.0,
                payer_name='Second Customer',
            )
            # Since TX1 is already unmatched with 30000.0, auto-match must refuse for TX2!
            self.assertEqual(tx2.matching_status, 'unmatched')
            self.assertFalse(tx2.pos_order_id)
            mock_broadcast.assert_called_with(
                event_type='seerbit_va_payment_received',
                data={
                    'transaction_id': tx2.id,
                    'reference': tx2.reference,
                    'amount': tx2.amount,
                    'payer_name': tx2.payer_name,
                    'account_number': tx2.account_number,
                    'payment_date': fields.Datetime.to_string(tx2.payment_date),
                    'has_multiple_same_amount': True,
                    'requires_manual_match': True,
                }
            )

        # 3. Verify get_unmatched_payments_for_pos marks both with has_duplicate_amount = True
        payments = self.env['seerbit.va.transaction'].get_unmatched_payments_for_pos(va.id)
        self.assertEqual(len(payments), 2)
        for p in payments:
            self.assertTrue(p['has_duplicate_amount'])

        # 4. Cashier manually selects one of the duplicate payments
        res = self.env['seerbit.va.transaction'].match_payment_to_pos_order_atomic(
            tx1.id,
            order.pos_reference
        )
        self.assertTrue(res['success'])
        self.assertEqual(tx1.matching_status, 'manual_matched')
        self.assertEqual(tx1.pos_order_ref, order.pos_reference)

        # Now only TX2 is unmatched, so has_duplicate_amount becomes False
        payments_after = self.env['seerbit.va.transaction'].get_unmatched_payments_for_pos(va.id)
        unmatched_after = [p for p in payments_after if p['matching_status'] == 'unmatched']
        self.assertEqual(len(unmatched_after), 1)
        self.assertFalse(unmatched_after[0]['has_duplicate_amount'])

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
                    'account_number': tx.account_number,
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
            self.assertEqual(next_action.get('type'), 'ir.actions.client')
            self.assertEqual(next_action.get('tag'), 'reload')

    def test_action_generate_static_va_without_company_email(self):
        company_no_email = self.env['res.company'].create({
            'name': 'Labule No Email Co',
            'seerbit_public_key': 'KEY_TEST_NO_EMAIL',
            'seerbit_secret_key': 'SEC_TEST_NO_EMAIL',
        })
        company_no_email.partner_id.write({'email': False})
        company_no_email.write({'email': False})

        pm = self.env['pos.payment.method'].create({
            'name': 'Till Transfer 1',
            'company_id': company_no_email.id,
            'payment_method_type': 'transfer',
            'seerbit_transfer_type': 'static',
        })

        mock_response = {
            'code': 'S20',
            'payments': {
                'walletName': 'Till Transfer 1',
                'accountNumber': '7788990011',
                'bankName': 'Providus Bank',
                'reference': 'POS_STATIC_NE_1',
            }
        }
        with patch('odoo.addons.pos_seerbit.services.seerbit_api.SeerbitAPI.create_virtual_account', return_value=mock_response) as mock_create:
            res = pm.action_generate_static_va()
            self.assertTrue(pm.seerbit_va_id)
            self.assertEqual(pm.seerbit_va_account_number, '7788990011')
            self.assertEqual(pm.seerbit_va_bank_name, 'Providus Bank')
            # Verify call args had fullName with name + ID, and fallback email
            called_kwargs = mock_create.call_args[1]
            self.assertIn('Till Transfer 1', called_kwargs['full_name'])
            self.assertIn(f'Till {pm.id}', called_kwargs['full_name'])
            self.assertTrue(called_kwargs['email'].endswith('@pos.seerbit.com'))

    def test_regenerate_static_va_creates_new_account_and_updates_pm(self):
        company = self.company_a
        pm = self.env['pos.payment.method'].create({
            'name': 'Regen Till',
            'company_id': company.id,
            'payment_method_type': 'transfer',
            'seerbit_transfer_type': 'static',
        })
        # Mock initial VA
        old_va = self.env['seerbit.virtual.account'].create({
            'name': 'Old VA',
            'company_id': company.id,
            'account_number': '1111222233',
            'bank_name': 'Old Bank',
            'reference': 'POS_STATIC_OLD',
            'va_type': 'pos_static',
        })
        pm.seerbit_va_id = old_va.id

        # Now regenerate
        mock_new_response = {
            'code': 'S20',
            'payments': {
                'walletName': 'Regen Till New',
                'accountNumber': '9999888877',
                'bankName': 'New Bank',
                'reference': 'POS_STATIC_NEW_2',
            }
        }
        with patch('odoo.addons.pos_seerbit.services.seerbit_api.SeerbitAPI.create_virtual_account', return_value=mock_new_response) as mock_create:
            res = pm.action_generate_static_va()
            # Assert pm now points to the new VA
            self.assertNotEqual(pm.seerbit_va_id.id, old_va.id)
            self.assertEqual(pm.seerbit_va_account_number, '9999888877')
            self.assertEqual(pm.seerbit_va_bank_name, 'New Bank')
            # Assert old VA is still preserved in db
            self.assertTrue(old_va.exists())
            self.assertEqual(old_va.account_number, '1111222233')
            # Verify call arguments
            called_kwargs = mock_create.call_args[1]
            self.assertIn('Regen Till', called_kwargs['full_name'])
            self.assertIn(f'Till {pm.id}', called_kwargs['full_name'])

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
                    'walletName': self.company_a.name,
                    'bankName': 'Wema Bank',
                    'accountNumber': '8877665544',
                    'reference': 'POS_DYN_POS_001_1234',
                }
            }
        }
        with patch('odoo.addons.pos_seerbit.services.seerbit_api.SeerbitAPI.create_dynamic_virtual_account', return_value=mock_dynamic_response['data']) as mock_create:
            res = pm.create_dynamic_va_for_pos(
                order_ref="POS_001",
                amount=7500.0,
                customer_name="Jane Doe",
                customer_email="jane@example.com"
            )
            self.assertEqual(res['account_number'], '8877665544')
            self.assertEqual(res['bank_name'], 'Wema Bank')
            self.assertEqual(res['amount'], 7500.0)
            self.assertEqual(mock_create.call_args.kwargs['customer_name'], self.company_a.name[:50].strip())
            self.assertIn("@", mock_create.call_args.kwargs['customer_email'])

            # Ensure the dynamic VA was saved in seerbit.virtual.account with va_type='pos_dynamic'
            va = self.env['seerbit.virtual.account'].browse(res['va_id'])
            self.assertTrue(va.exists())
            self.assertEqual(va.va_type, 'pos_dynamic')
            self.assertEqual(va.account_number, '8877665544')

    def test_seerbit_api_create_dynamic_virtual_account_initiates_endpoint(self):
        """Verify SeerbitAPI.create_dynamic_virtual_account calls Checkout Transfer /initiates endpoint."""
        from odoo.addons.pos_seerbit.services.seerbit_api import SeerbitAPI
        api_client = SeerbitAPI(self.env, company=self.company_a)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '{"status":"SUCCESS"}'
        mock_resp.json.return_value = {
            'status': 'SUCCESS',
            'data': {
                'code': '00',
                'payments': {
                    'accountNumber': '9876543210',
                    'bankName': 'Moniepoint MFB',
                    'paymentReference': 'POS_DYN_TEST_REF_123',
                }
            }
        }
        with patch.object(api_client, '_do_request', return_value=mock_resp) as mock_req:
            res = api_client.create_dynamic_virtual_account(
                reference="POS_DYN_TEST_REF_123",
                amount=5000.0,
                customer_name="Transfer Payer",
                customer_email="transfer@example.com",
            )
            self.assertEqual(mock_req.call_count, 1)
            args, kwargs = mock_req.call_args
            self.assertEqual(args[0], 'POST')
            self.assertEqual(args[1], 'https://seerbitapi.com/api/v2/payments/initiates')
            payload = kwargs.get('json', {})
            self.assertEqual(payload.get('paymentType'), 'TRANSFER')
            self.assertEqual(payload.get('paymentReference'), 'POS_DYN_TEST_REF_123')
            self.assertEqual(payload.get('amount'), '5000.0')
            self.assertEqual(res.get('payments', {}).get('accountNumber'), '9876543210')

    def test_create_dynamic_va_unique_tokens_and_email_tagging(self):
        """Verify dynamic VA generation tags email and adds unique token to prevent S12 collision."""
        pm = self.env['pos.payment.method'].create({
            'name': 'Dynamic Method POS Unique',
            'company_id': self.company_a.id,
            'payment_method_type': 'transfer',
            'seerbit_transfer_type': 'dynamic',
            'journal_id': self.journal_a.id,
        })
        captured_payloads = []
        def fake_create_dyn(reference, amount, customer_name, customer_email, expiry_minutes=30, **kwargs):
            captured_payloads.append({
                'reference': reference,
                'email': customer_email,
                'amount': amount,
            })
            return {
                'payments': {
                    'accountNumber': '1020304050',
                    'bankName': 'Wema Bank',
                    'paymentReference': reference,
                }
            }

        with patch('odoo.addons.pos_seerbit.services.seerbit_api.SeerbitAPI.create_dynamic_virtual_account', side_effect=fake_create_dyn):
            res1 = pm.create_dynamic_va_for_pos("ORD100", 2500.0, customer_name="Alice", customer_email="alice@example.com")
            res2 = pm.create_dynamic_va_for_pos("ORD100", 3000.0, customer_name="Alice", customer_email="alice@example.com")

            self.assertEqual(len(captured_payloads), 2)
            # References must differ
            self.assertNotEqual(captured_payloads[0]['reference'], captured_payloads[1]['reference'])
            # Emails must have unique tags to bypass Seerbit 1-account-per-customer restriction
            self.assertIn('alice+', captured_payloads[0]['email'])
            self.assertIn('alice+', captured_payloads[1]['email'])
            self.assertNotEqual(captured_payloads[0]['email'], captured_payloads[1]['email'])

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

    def test_inter_till_transfer_workflow(self):
        """
        Verify transferring an unmatched transaction from Till 1 to Till 2:
        - Source transaction becomes 'transferred' and remains linked.
        - Destination transaction is created for Till 2's VA as 'unmatched'.
        - Spooling/re-registration does not overwrite 'transferred'.
        - Atomic matching on the transferred source transaction is forbidden.
        """
        va1 = self.env['seerbit.virtual.account'].create({
            'name': 'VA Store Till 1',
            'company_id': self.company_a.id,
            'partner_id': self.company_a.partner_id.id,
            'account_number': '1112220001',
            'bank_name': 'Test Bank 1',
            'va_type': 'pos_static',
        })
        va2 = self.env['seerbit.virtual.account'].create({
            'name': 'VA Store Till 2',
            'company_id': self.company_a.id,
            'partner_id': self.company_a.partner_id.id,
            'account_number': '1112220002',
            'bank_name': 'Test Bank 2',
            'va_type': 'pos_static',
        })

        pm1 = self.env['pos.payment.method'].create({
            'name': 'Transfer Till 1',
            'company_id': self.company_a.id,
            'payment_method_type': 'transfer',
            'seerbit_transfer_type': 'static',
            'seerbit_va_id': va1.id,
        })
        pm2 = self.env['pos.payment.method'].create({
            'name': 'Transfer Till 2',
            'company_id': self.company_a.id,
            'payment_method_type': 'transfer',
            'seerbit_transfer_type': 'static',
            'seerbit_va_id': va2.id,
        })

        till1 = self.env['pos.config'].create({
            'name': 'POS Till 1',
            'company_id': self.company_a.id,
            'payment_method_ids': [(6, 0, [pm1.id])],
        })
        till2 = self.env['pos.config'].create({
            'name': 'POS Till 2',
            'company_id': self.company_a.id,
            'payment_method_ids': [(6, 0, [pm2.id])],
        })

        # Test get_available_tills_for_transfer
        available = self.env['seerbit.va.transaction'].get_available_tills_for_transfer(current_config_id=till1.id)
        till2_data = [t for t in available if t['id'] == till2.id]
        self.assertTrue(till2_data)
        self.assertEqual(till2_data[0]['payment_methods'][0]['account_number'], '1112220002')

        # Create source transaction on VA 1
        tx1 = self.env['seerbit.va.transaction'].create({
            'name': 'TX_ORIGINAL',
            'va_id': va1.id,
            'reference': 'REF_TRF_TEST_101',
            'amount': 7500.0,
            'payer_name': 'Accidental Sender',
            'status': 'successful',
            'matching_status': 'unmatched',
        })

        # Execute transfer to Till 2
        res = self.env['seerbit.va.transaction'].transfer_payment_to_till(
            transaction_id=tx1.id,
            target_config_id=till2.id,
            target_pm_id=pm2.id
        )
        self.assertTrue(res.get('success'))
        self.assertEqual(res.get('dest_till_name'), till2.name)

        # 1. Source transaction checks
        tx1.invalidate_recordset()
        self.assertEqual(tx1.matching_status, 'transferred')
        self.assertEqual(tx1.transferred_to_config_id.id, till2.id)
        self.assertEqual(tx1.transferred_to_va_id.id, va2.id)
        self.assertTrue(tx1.transferred_to_tx_id)

        # 2. Destination transaction checks
        dest_tx = tx1.transferred_to_tx_id
        self.assertEqual(dest_tx.va_id.id, va2.id)
        self.assertEqual(dest_tx.amount, 7500.0)
        self.assertEqual(dest_tx.payer_name, 'Accidental Sender')
        self.assertEqual(dest_tx.matching_status, 'unmatched')
        self.assertTrue(dest_tx.is_transferred_in)
        self.assertEqual(dest_tx.transferred_from_tx_id.id, tx1.id)

        # 3. get_unmatched_payments_for_pos on Till 1 returns it as 'transferred'
        payments_till1 = self.env['seerbit.va.transaction'].get_unmatched_payments_for_pos(va1.id)
        p1 = [p for p in payments_till1 if p['id'] == tx1.id][0]
        self.assertEqual(p1['matching_status'], 'transferred')
        self.assertEqual(p1['transferred_to_config_name'], till2.name)

        # 4. get_unmatched_payments_for_pos on Till 2 returns it as active unmatched
        payments_till2 = self.env['seerbit.va.transaction'].get_unmatched_payments_for_pos(va2.id)
        p2 = [p for p in payments_till2 if p['id'] == dest_tx.id][0]
        self.assertEqual(p2['matching_status'], 'unmatched')
        self.assertTrue(p2['is_transferred_in'])

        # 5. Idempotent re-spooling does not overwrite 'transferred'
        re_spooled = self.env['seerbit.va.transaction'].register_inbound_transaction(
            va=va1,
            reference=tx1.reference,
            amount=7500.0,
            payer_name='Accidental Sender'
        )
        self.assertEqual(re_spooled.matching_status, 'transferred')

        # 6. Attempting to match the transferred source payment is rejected
        match_res = self.env['seerbit.va.transaction'].match_payment_to_pos_order_atomic(
            transaction_id=tx1.id,
            pos_order_uuid='DUMMY_ORDER_123'
        )
        self.assertFalse(match_res.get('success'))



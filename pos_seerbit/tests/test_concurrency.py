# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase, tagged
from odoo.exceptions import ValidationError


@tagged('post_install', '-at_install', 'pos_seerbit')
class TestSeerbitConcurrency(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.va = cls.env['seerbit.virtual.account'].create({
            'name': 'Till 1 VA',
            'company_id': cls.company.id,
            'account_number': '9901122334',
            'bank_name': 'Providus Bank',
            'reference': 'VA_TILL_1',
            'va_type': 'pos_static',
        })
        cls.tx = cls.env['seerbit.va.transaction'].create({
            'name': 'TX001',
            'va_id': cls.va.id,
            'reference': 'SB_TEST_CONC_1',
            'amount': 25000.0,
            'payer_name': 'John Doe',
            'status': 'successful',
            'matching_status': 'unmatched',
        })

    def test_atomic_matching_success_and_collision_prevention(self):
        # 1. Cashier A matches the transaction to Order A
        res_a = self.env['seerbit.va.transaction'].match_payment_to_pos_order_atomic(
            transaction_id=self.tx.id,
            pos_order_uuid='ORDER_AAA_001',
            cashier_user_id=self.env.uid,
        )
        self.assertTrue(res_a.get('success'))
        self.assertEqual(res_a.get('reference'), 'SB_TEST_CONC_1')
        self.assertEqual(self.tx.matching_status, 'manual_matched')
        self.assertEqual(self.tx.pos_order_ref, 'ORDER_AAA_001')

        # 2. Cashier B attempts to match the same transaction to Order B simultaneously
        res_b = self.env['seerbit.va.transaction'].match_payment_to_pos_order_atomic(
            transaction_id=self.tx.id,
            pos_order_uuid='ORDER_BBB_002',
            cashier_user_id=self.env.uid,
        )
        self.assertFalse(res_b.get('success'))
        self.assertIn("already been matched", res_b.get('error', ''))
        # Transaction remains matched to Order A
        self.assertEqual(self.tx.pos_order_ref, 'ORDER_AAA_001')

    def test_atomic_matching_not_found(self):
        res = self.env['seerbit.va.transaction'].match_payment_to_pos_order_atomic(
            transaction_id=99999999,
            pos_order_uuid='ORDER_NONEXISTENT',
        )
        self.assertFalse(res.get('success'))
        self.assertIn("not found", res.get('error', '').lower())


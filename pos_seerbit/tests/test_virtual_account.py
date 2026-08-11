# -*- coding: utf-8 -*-
from unittest.mock import patch

from odoo.tests.common import tagged

from .common import SeerbitCommon


@tagged('post_install', '-at_install', 'pos_seerbit')
class TestSeerbitVirtualAccount(SeerbitCommon):

    def test_create_va_pins_company(self):
        api_patch, mock_api = self._mock_seerbit_api()
        with api_patch:
            self.partner_a.action_create_seerbit_va()

        va = self.env['seerbit.virtual.account'].search([
            ('partner_id', '=', self.partner_a.id),
        ], limit=1)
        self.assertTrue(va)
        self.assertEqual(va.company_id, self.company_a)
        self.assertEqual(va.account_number, '9990011223')
        mock_api.create_virtual_account.assert_called()

    def test_generate_va_on_record_uses_company_id(self):
        va = self.env['seerbit.virtual.account'].create({
            'partner_id': self.partner_b.id,
            'company_id': self.company_b.id,
        })
        api_patch, mock_api = self._mock_seerbit_api()
        with api_patch:
            va.action_generate_va()
        self.assertEqual(va.company_id, self.company_b)
        self.assertEqual(va.account_number, '9990011223')
        self.assertEqual(va.bank_name, 'Seerbit Test Bank')

    def test_process_va_payment_respects_auto_flags(self):
        va = self.env['seerbit.virtual.account'].create({
            'partner_id': self.partner_b.id,
            'company_id': self.company_b.id,
            'account_number': '8880001111',
            'bank_name': 'Test Bank',
            'reference': 'VA_B_1',
            'name': 'Branch B VA',
        })
        # company_b has auto_post=False → payment stays draft
        va._process_seerbit_va_payment(500.0, 'REF_VA_B_NOPOST')
        payment = self.env['account.payment'].search([
            ('seerbit_va_id', '=', va.id),
            ('memo', '=', 'REF_VA_B_NOPOST'),
        ], limit=1)
        self.assertTrue(payment)
        self.assertEqual(payment.company_id, self.company_b)
        self.assertEqual(payment.state, 'draft')

    def test_process_va_payment_posts_when_enabled(self):
        va = self.env['seerbit.virtual.account'].create({
            'partner_id': self.partner_a.id,
            'company_id': self.company_a.id,
            'account_number': '7770001111',
            'bank_name': 'Test Bank',
            'reference': 'VA_A_1',
            'name': 'Branch A VA',
        })
        invoice = self._create_invoice(self.company_a, self.partner_a, 250.0)
        va._process_seerbit_va_payment(250.0, 'REF_VA_A_POST')
        payment = self.env['account.payment'].search([
            ('seerbit_va_id', '=', va.id),
            ('memo', '=', 'REF_VA_A_POST'),
        ], limit=1)
        self.assertTrue(payment)
        self.assertEqual(payment.company_id, self.company_a)
        self.assertIn(payment.state, ('posted', 'in_process', 'paid'))
        # Invoice may be reconciled depending on AR matching
        self.assertTrue(invoice.exists())

    def test_cron_groups_by_company(self):
        va_a = self.env['seerbit.virtual.account'].create({
            'partner_id': self.partner_a.id,
            'company_id': self.company_a.id,
            'account_number': '1112223334',
            'reference': 'VA_CRON_A',
            'name': 'Cron A',
        })
        va_b = self.env['seerbit.virtual.account'].create({
            'partner_id': self.partner_b.id,
            'company_id': self.company_b.id,
            'account_number': '1112223335',
            'reference': 'VA_CRON_B',
            'name': 'Cron B',
        })
        companies_seen = []

        class TrackingAPI:
            def __init__(self, env, company=None):
                companies_seen.append(company.id if company else None)
                self.company = company

            def get_virtual_account_payments(self, account_number):
                return []

        with patch(
            'odoo.addons.pos_seerbit.services.seerbit_api.SeerbitAPI',
            TrackingAPI,
        ):
            self.env['seerbit.virtual.account']._cron_spool_seerbit_payments()

        self.assertIn(self.company_a.id, companies_seen)
        self.assertIn(self.company_b.id, companies_seen)
        self.assertTrue(va_a.exists() and va_b.exists())

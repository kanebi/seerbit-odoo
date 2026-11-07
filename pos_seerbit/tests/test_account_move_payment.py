# -*- coding: utf-8 -*-
from unittest.mock import patch

from odoo.tests.common import tagged

from .common import SeerbitCommon


@tagged('post_install', '-at_install', 'pos_seerbit')
class TestSeerbitAccountMovePayment(SeerbitCommon):

    def test_process_pos_payment_uses_invoice_company(self):
        invoice = self._create_invoice(self.company_a, self.partner_a, 1200.0)
        invoice.action_process_seerbit_pos_payment('POS_TX_001', 1200.0)
        payment = self.env['account.payment'].search([
            ('memo', '=', 'POS_TX_001'),
            ('partner_id', '=', self.partner_a.id),
        ], limit=1)
        self.assertTrue(payment)
        self.assertEqual(payment.company_id, self.company_a)
        self.assertIn(payment.state, ('posted', 'in_process', 'paid'))

    def test_check_seerbit_status_creates_payment(self):
        invoice = self._create_invoice(self.company_a, self.partner_a, 900.0)
        invoice.write({
            'synced_with_seerbit': True,
            'seerbit_invoice_no': 'SB_INV_900',
        })
        api_patch, mock_api = self._mock_seerbit_api()
        with api_patch:
            invoice.action_check_seerbit_status()
        self.assertEqual(invoice.seerbit_invoice_status, 'PAID')
        payment = self.env['account.payment'].search([
            ('memo', '=', 'Sync: SB_INV_900'),
        ], limit=1)
        self.assertTrue(payment)
        self.assertEqual(payment.company_id, self.company_a)

    def test_check_status_respects_auto_post_false(self):
        self.company_a.sudo().write({'seerbit_auto_post': False})
        invoice = self._create_invoice(self.company_a, self.partner_a, 400.0)
        invoice.write({
            'synced_with_seerbit': True,
            'seerbit_invoice_no': 'SB_INV_400',
        })
        api_patch, mock_api = self._mock_seerbit_api()
        mock_api.get_invoice.return_value = {'status': 'PAID', 'totalAmount': 400.0}
        with api_patch:
            invoice.action_check_seerbit_status()
        payment = self.env['account.payment'].search([
            ('memo', '=', 'Sync: SB_INV_400'),
        ], limit=1)
        self.assertTrue(payment)
        self.assertEqual(payment.state, 'draft')
        self.company_a.sudo().write({'seerbit_auto_post': True})

    def test_sync_invoice_calls_api_with_company(self):
        invoice = self._create_invoice(self.company_a, self.partner_a, 500.0)
        companies = []

        class TrackingAPI:
            def __init__(self, env, company=None):
                companies.append(company.id if company else None)

            def get_invoice(self, *a, **k):
                return False

            def create_invoice(self, **kwargs):
                return {'InvoiceNo': 'NEW_SB_INV'}

            def delete_invoice(self, *a, **k):
                return True

        with patch(
            'odoo.addons.pos_seerbit.services.seerbit_api.SeerbitAPI',
            TrackingAPI,
        ):
            invoice.action_sync_seerbit_invoice()
        self.assertEqual(invoice.seerbit_invoice_no, 'NEW_SB_INV')
        self.assertTrue(invoice.synced_with_seerbit)
        self.assertIn(self.company_a.id, companies)

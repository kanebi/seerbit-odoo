# -*- coding: utf-8 -*-
from odoo.tests.common import tagged

from .common import SeerbitCommon


@tagged('post_install', '-at_install', 'pos_seerbit')
class TestSeerbitPaymentLink(SeerbitCommon):

    def test_create_link_from_invoice_sets_company(self):
        invoice = self._create_invoice(self.company_a, self.partner_a, 1500.0)
        api_patch, mock_api = self._mock_seerbit_api()
        with api_patch:
            link = self.env['pos_seerbit.payment.link'].create({
                'move_id': invoice.id,
                'partner_id': self.partner_a.id,
                'email': self.partner_a.email,
                'amount': 1500.0,
                'description': invoice.name,
            })
        self.assertEqual(link.company_id, self.company_a)
        self.assertEqual(link.seerbit_link_id, 'PL_TEST_123')
        self.assertTrue(link.link_url)
        mock_api.create_payment_link.assert_called_once()

    def test_create_standalone_link_uses_env_company(self):
        api_patch, mock_api = self._mock_seerbit_api()
        with api_patch:
            link = self.env['pos_seerbit.payment.link'].create({
                'company_id': self.company_b.id,
                'partner_id': self.partner_b.id,
                'email': self.partner_b.email,
                'amount': 200.0,
                'description': 'Standalone',
                'link_url': 'https://pay.seerbit.test/standalone',
                'seerbit_link_id': 'PL_STANDALONE_B',
            })
        self.assertEqual(link.company_id, self.company_b)
        self.assertEqual(link.state, 'pending')

    def test_process_payment_posts_for_company_a(self):
        invoice = self._create_invoice(self.company_a, self.partner_a, 800.0)
        api_patch, mock_api = self._mock_seerbit_api()
        with api_patch:
            link = self.env['pos_seerbit.payment.link'].create({
                'move_id': invoice.id,
                'company_id': self.company_a.id,
                'partner_id': self.partner_a.id,
                'amount': 800.0,
                'link_url': 'https://pay.seerbit.test/x',
                'seerbit_link_id': 'PL_EXISTING',
            })
        link._process_payment(800.0, 'LINK_PAY_REF_1')
        self.assertEqual(link.state, 'paid')
        self.assertTrue(link.payment_id)
        self.assertEqual(link.payment_id.company_id, self.company_a)
        self.assertIn(link.payment_id.state, ('posted', 'in_process', 'paid'))

    def test_process_payment_respects_auto_post_false(self):
        self.company_a.sudo().write({'seerbit_auto_post': False})
        invoice = self._create_invoice(self.company_a, self.partner_a, 300.0)
        link = self.env['pos_seerbit.payment.link'].create({
            'move_id': invoice.id,
            'company_id': self.company_a.id,
            'partner_id': self.partner_a.id,
            'amount': 300.0,
            'link_url': 'https://pay.seerbit.test/y',
            'seerbit_link_id': 'PL_A_NOPOST',
        })
        link._process_payment(300.0, 'LINK_PAY_REF_NOPOST')
        self.assertEqual(link.state, 'paid')
        self.assertEqual(link.payment_id.state, 'draft')
        self.company_a.sudo().write({'seerbit_auto_post': True})

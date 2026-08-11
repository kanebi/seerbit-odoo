# -*- coding: utf-8 -*-
"""Shared fixtures for pos_seerbit tests."""
from unittest.mock import MagicMock, patch

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'pos_seerbit')
class SeerbitCommon(TransactionCase):
    """Base case with two companies and Seerbit business config."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Company = cls.env['res.company']
        cls.company_a = cls.env.company
        cls.company_b = cls.Company.create({
            'name': 'Seerbit Test Branch B',
            'currency_id': cls.company_a.currency_id.id,
        })

        # Distinct keys per company (webhook uniqueness)
        cls.company_a.sudo().write({
            'seerbit_public_key': 'SB_PUB_TEST_A',
            'seerbit_secret_key': 'SB_SEC_TEST_A',
            'seerbit_pocket_id': 'SBP_A',
            'seerbit_pocket_email': 'pocket-a@example.com',
            'seerbit_pocket_password': 'secret-a',
            'seerbit_auto_post': True,
            'seerbit_auto_reconcile': True,
        })
        cls.company_b.sudo().write({
            'seerbit_public_key': 'SB_PUB_TEST_B',
            'seerbit_secret_key': 'SB_SEC_TEST_B',
            'seerbit_pocket_id': 'SBP_B',
            'seerbit_pocket_email': 'pocket-b@example.com',
            'seerbit_pocket_password': 'secret-b',
            'seerbit_auto_post': False,
            'seerbit_auto_reconcile': False,
        })

        # Journals
        from odoo.addons.pos_seerbit.hooks import ensure_seerbit_journal
        cls.journal_a = ensure_seerbit_journal(cls.env, cls.company_a)
        cls.journal_b = ensure_seerbit_journal(cls.env, cls.company_b)

        cls.partner_a = cls.env['res.partner'].with_context(
            labule_skip_seerbit_va=True,
        ).create({
            'name': 'Seerbit Customer A',
            'email': 'customer-a@example.com',
            'company_id': cls.company_a.id,
            'customer_rank': 1,
        })
        cls.partner_b = cls.env['res.partner'].with_context(
            labule_skip_seerbit_va=True,
        ).create({
            'name': 'Seerbit Customer B',
            'email': 'customer-b@example.com',
            'company_id': cls.company_b.id,
            'customer_rank': 1,
        })

        cls.product = cls.env['product.product'].create({
            'name': 'Seerbit Test Product',
            'list_price': 1000.0,
            'type': 'service',
            'invoice_policy': 'order',
        })

    def _create_invoice(self, company, partner, amount=1000.0):
        move = self.env['account.move'].with_company(company).create({
            'move_type': 'out_invoice',
            'partner_id': partner.id,
            'company_id': company.id,
            'invoice_line_ids': [(0, 0, {
                'product_id': self.product.id,
                'quantity': 1,
                'price_unit': amount,
                'name': 'Test line',
            })],
        })
        move.action_post()
        return move

    def _mock_seerbit_api(self):
        """Return a patch context manager for SeerbitAPI with safe stubs."""
        mock_api = MagicMock()
        mock_api.create_payment_link.return_value = {
            'paymentLinkUrl': 'https://pay.seerbit.test/link/abc',
            'paymentLinkId': 'PL_TEST_123',
        }
        mock_api.create_virtual_account.return_value = {
            'payments': {
                'reference': 'VA_REF_TEST',
                'accountNumber': '9990011223',
                'bankName': 'Seerbit Test Bank',
                'walletName': 'Test VA Wallet',
            }
        }
        mock_api.get_invoice.return_value = {
            'status': 'PAID',
            'totalAmount': 1000.0,
        }
        mock_api.create_invoice.return_value = {'InvoiceNo': 'INV_SB_001'}
        mock_api.get_virtual_account_payments.return_value = []
        mock_api.delete_virtual_account.return_value = True
        mock_api.delete_payment_link.return_value = True
        mock_api.update_payment_link.return_value = True

        return patch(
            'odoo.addons.pos_seerbit.services.seerbit_api.SeerbitAPI',
            return_value=mock_api,
        ), mock_api

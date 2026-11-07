# -*- coding: utf-8 -*-
import json

from odoo.tests.common import HttpCase, tagged


@tagged('post_install', '-at_install', 'pos_seerbit')
class TestSeerbitWebhook(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.sudo().write({
            'seerbit_public_key': 'SB_WH_PUB',
            'seerbit_secret_key': 'SB_WH_SEC',
            'seerbit_auto_post': True,
            'seerbit_auto_reconcile': True,
        })
        from odoo.addons.pos_seerbit.hooks import ensure_seerbit_journal
        cls.journal = ensure_seerbit_journal(cls.env, cls.company)

        cls.partner = cls.env['res.partner'].with_context(
            labule_skip_seerbit_va=True,
        ).create({
            'name': 'Webhook Customer',
            'email': 'wh@example.com',
            'customer_rank': 1,
        })
        product = cls.env['product.product'].create({
            'name': 'WH Product',
            'list_price': 500.0,
            'type': 'service',
        })
        cls.invoice = cls.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': cls.partner.id,
            'company_id': cls.company.id,
            'invoice_line_ids': [(0, 0, {
                'product_id': product.id,
                'quantity': 1,
                'price_unit': 500.0,
                'name': 'WH line',
            })],
        })
        cls.invoice.action_post()
        cls.invoice.write({
            'seerbit_invoice_no': 'WH_INV_500',
            'synced_with_seerbit': True,
        })

        cls.va = cls.env['seerbit.virtual.account'].create({
            'partner_id': cls.partner.id,
            'company_id': cls.company.id,
            'account_number': '5556667778',
            'bank_name': 'WH Bank',
            'reference': 'WH_VA',
            'name': 'WH VA',
        })

        cls.link = cls.env['pos_seerbit.payment.link'].create({
            'move_id': cls.invoice.id,
            'company_id': cls.company.id,
            'partner_id': cls.partner.id,
            'amount': 500.0,
            'link_url': 'https://pay.seerbit.test/wh',
            'seerbit_link_id': '999888',
        })

    def _post_webhook(self, data):
        payload = {
            'notificationItems': [{
                'notificationRequestItem': {
                    'eventType': 'transaction',
                    'data': data,
                }
            }]
        }
        return self.url_open(
            '/api/seerbit/webhook',
            data=json.dumps(payload),
            headers={'Content-Type': 'application/json'},
        )

    def test_webhook_invoice_payment(self):
        resp = self._post_webhook({
            'invoiceNumber': 'WH_INV_500',
            'amount': 500.0,
            'reference': 'WH_REF_INV_1',
            'gatewayCode': '00',
            'publicKey': 'SB_WH_PUB',
        })
        self.assertEqual(resp.status_code, 200)
        payment = self.env['account.payment'].sudo().search([
            ('memo', '=', 'WH_REF_INV_1'),
        ], limit=1)
        self.assertTrue(payment)
        self.assertEqual(payment.company_id, self.company)

    def test_webhook_va_payment(self):
        resp = self._post_webhook({
            'creditAccountNumber': '5556667778',
            'amount': 250.0,
            'reference': 'WH_REF_VA_1',
            'gatewayCode': '00',
            'publicKey': 'SB_WH_PUB',
        })
        self.assertEqual(resp.status_code, 200)
        payment = self.env['account.payment'].sudo().search([
            ('seerbit_va_id', '=', self.va.id),
            ('memo', '=', 'WH_REF_VA_1'),
        ], limit=1)
        self.assertTrue(payment)

    def test_webhook_pos_va_payment(self):
        pos_va = self.env['seerbit.virtual.account'].create({
            'name': 'POS Till Static VA',
            'company_id': self.company.id,
            'account_number': '8889990001',
            'bank_name': 'Providus Bank',
            'reference': 'WH_POS_VA',
            'va_type': 'pos_static',
        })
        resp = self._post_webhook({
            'creditAccountNumber': '8889990001',
            'amount': 350.0,
            'reference': 'WH_REF_POS_VA_1',
            'customerName': 'Pos Payer Jane',
            'gatewayCode': '00',
            'publicKey': 'SB_WH_PUB',
        })
        self.assertEqual(resp.status_code, 200)

        # Staged in seerbit.va.transaction
        tx = self.env['seerbit.va.transaction'].sudo().search([
            ('reference', '=', 'WH_REF_POS_VA_1'),
        ], limit=1)
        self.assertTrue(tx)
        self.assertEqual(tx.va_id, pos_va)
        self.assertEqual(tx.amount, 350.0)
        self.assertEqual(tx.payer_name, 'Pos Payer Jane')
        self.assertEqual(tx.status, 'successful')

        # Accounting invoice flow is untouched: no account.payment created for this POS VA
        payment = self.env['account.payment'].sudo().search([
            ('memo', '=', 'WH_REF_POS_VA_1'),
        ], limit=1)
        self.assertFalse(payment)

    def test_webhook_pos_va_idempotency(self):
        pos_va = self.env['seerbit.virtual.account'].create({
            'name': 'POS Idempotency VA',
            'company_id': self.company.id,
            'account_number': '8889990002',
            'bank_name': 'Providus Bank',
            'reference': 'WH_POS_IDEM_VA',
            'va_type': 'pos_static',
        })
        # First delivery
        resp1 = self._post_webhook({
            'creditAccountNumber': '8889990002',
            'amount': 500.0,
            'reference': 'WH_REF_IDEM_1',
            'customerName': 'Idem User',
            'gatewayCode': '00',
            'publicKey': 'SB_WH_PUB',
        })
        self.assertEqual(resp1.status_code, 200)

        # Duplicate delivery
        resp2 = self._post_webhook({
            'creditAccountNumber': '8889990002',
            'amount': 500.0,
            'reference': 'WH_REF_IDEM_1',
            'customerName': 'Idem User',
            'gatewayCode': '00',
            'publicKey': 'SB_WH_PUB',
        })
        self.assertEqual(resp2.status_code, 200)

        count = self.env['seerbit.va.transaction'].sudo().search_count([
            ('reference', '=', 'WH_REF_IDEM_1'),
        ])
        self.assertEqual(count, 1)

    def test_webhook_payment_link(self):
        # Fresh unpaid invoice + link to avoid already-paid state from prior tests
        invoice2 = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.partner.id,
            'company_id': self.company.id,
            'invoice_line_ids': [(0, 0, {
                'name': 'Link line',
                'quantity': 1,
                'price_unit': 100.0,
            })],
        })
        invoice2.action_post()
        link2 = self.env['pos_seerbit.payment.link'].create({
            'move_id': invoice2.id,
            'company_id': self.company.id,
            'partner_id': self.partner.id,
            'amount': 100.0,
            'link_url': 'https://pay.seerbit.test/wh2',
            'seerbit_link_id': '777666',
        })
        resp = self._post_webhook({
            'paymentLinkId': '777666',
            'amount': 100.0,
            'reference': 'WH_REF_LINK_1',
            'gatewayCode': '00',
            'publicKey': 'SB_WH_PUB',
        })
        self.assertEqual(resp.status_code, 200)
        link2.invalidate_recordset()
        self.assertEqual(link2.state, 'paid')

    def test_webhook_rejects_unknown_key_mismatch_on_invoice(self):
        # Wrong public key vs invoice company key → skipped (no new payment with this ref)
        before = self.env['account.payment'].sudo().search_count([])
        resp = self._post_webhook({
            'invoiceNumber': 'WH_INV_500',
            'amount': 10.0,
            'reference': 'WH_REF_BAD_KEY',
            'gatewayCode': '00',
            'publicKey': 'WRONG_KEY',
        })
        self.assertEqual(resp.status_code, 200)
        payment = self.env['account.payment'].sudo().search([
            ('memo', '=', 'WH_REF_BAD_KEY'),
        ], limit=1)
        self.assertFalse(payment)
        self.assertEqual(self.env['account.payment'].sudo().search_count([]), before)

    def test_pos_notification_resolves_company(self):
        pm = self.env['pos.payment.method'].create({
            'name': 'WH Seerbit PM',
            'use_payment_terminal': 'seerbit',
            'company_id': self.company.id,
            'journal_id': self.journal.id,
            'seerbit_terminal_id': 'WH_TERM_1',
        })
        # Resolve company the same way the controller does (no HTTP request proxy)
        company = self.env['res.company'].sudo().seerbit_company_by_public_key('SB_WH_PUB')
        self.assertEqual(company, self.company)
        payment_method = self.env['pos.payment.method'].sudo().search([
            ('company_id', '=', company.id),
            ('use_payment_terminal', '=', 'seerbit'),
            ('seerbit_terminal_id', '=', 'WH_TERM_1'),
        ], limit=1)
        self.assertEqual(payment_method, pm)
        notification = {
            'eventType': 'transaction',
            'data': {
                'publicKey': 'SB_WH_PUB',
                'code': '00',
                'transactionRef': 'POS_WH_1',
                'terminalId': 'WH_TERM_1',
            },
        }
        payment_method.seerbit_latest_response = json.dumps(notification)
        self.assertTrue(pm.seerbit_latest_response)

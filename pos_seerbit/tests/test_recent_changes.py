# -*- coding: utf-8 -*-
import json
from odoo.exceptions import ValidationError
from odoo.tests.common import HttpCase, tagged


@tagged('post_install', '-at_install', 'pos_seerbit')
class TestSeerbitRecentChanges(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Set up two companies with distinct keys/configs
        cls.company_a = cls.env.company
        cls.company_a.sudo().write({
            'seerbit_public_key': 'KEY_A_PUB',
            'seerbit_secret_key': 'KEY_A_SEC',
            'seerbit_auto_post': True,
            'seerbit_auto_reconcile': True,
        })

        cls.company_b = cls.env['res.company'].create({
            'name': 'Seerbit Test Company B',
            'currency_id': cls.company_a.currency_id.id,
        })
        cls.company_b.sudo().write({
            'seerbit_public_key': 'KEY_B_PUB',
            'seerbit_secret_key': 'KEY_B_SEC',
            'seerbit_auto_post': False,
            'seerbit_auto_reconcile': False,
        })

        # Ensure journals exist
        from odoo.addons.pos_seerbit.hooks import ensure_seerbit_journal
        cls.journal_a = ensure_seerbit_journal(cls.env, cls.company_a)
        cls.journal_b = ensure_seerbit_journal(cls.env, cls.company_b)

        # Create partners
        cls.partner_a = cls.env['res.partner'].with_context(
            labule_skip_seerbit_va=True,
        ).create({
            'name': 'Partner A',
            'email': 'partnera@example.com',
            'company_id': cls.company_a.id,
        })
        cls.partner_b = cls.env['res.partner'].with_context(
            labule_skip_seerbit_va=True,
        ).create({
            'name': 'Partner B',
            'email': 'partnerb@example.com',
            'company_id': cls.company_b.id,
        })

        # Create virtual accounts for both
        cls.va_a = cls.env['seerbit.virtual.account'].create({
            'partner_id': cls.partner_a.id,
            'company_id': cls.company_a.id,
            'account_number': '1111111111',
            'bank_name': 'Bank A',
            'reference': 'VA_REF_A',
            'name': 'VA A',
        })
        cls.va_b = cls.env['seerbit.virtual.account'].create({
            'partner_id': cls.partner_b.id,
            'company_id': cls.company_b.id,
            'account_number': '2222222222',
            'bank_name': 'Bank B',
            'reference': 'VA_REF_B',
            'name': 'VA B',
        })

        # Create payment links
        cls.link_a = cls.env['pos_seerbit.payment.link'].create({
            'company_id': cls.company_a.id,
            'partner_id': cls.partner_a.id,
            'amount': 300.0,
            'link_url': 'https://pay.seerbit.test/link_a',
            'seerbit_link_id': 'LINK_ID_A',
        })
        cls.link_b = cls.env['pos_seerbit.payment.link'].create({
            'company_id': cls.company_b.id,
            'partner_id': cls.partner_b.id,
            'amount': 400.0,
            'link_url': 'https://pay.seerbit.test/link_b',
            'seerbit_link_id': 'LINK_ID_B',
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

    def test_config_settings_validation(self):
        """Test Firestore credentials JSON validation constraint in settings."""
        settings = self.env['res.config.settings'].create({
            'module_pos_seerbit': True,
        })
        # Valid JSON format
        settings.seerbit_firestore_cred = '{"type": "service_account", "project_id": "test"}'
        # Should not raise
        settings._validate_firestore_cred()

        # Invalid JSON format
        with self.assertRaises(ValidationError):
            settings.seerbit_firestore_cred = '{invalid_json}'

    def test_webhook_va_routing_and_autopost(self):
        """Test webhook processing of VA payment, matching by public key & auto-post config."""
        # 1. Company A has auto_post = True. Send VA payment for company A.
        response = self._post_webhook({
            'creditAccountNumber': '1111111111',
            'amount': 150.0,
            'reference': 'WH_VA_A_SUCCESS',
            'gatewayCode': '00',
            'publicKey': 'KEY_A_PUB',
        })
        self.assertEqual(response.status_code, 200)
        payment_a = self.env['account.payment'].sudo().search([
            ('seerbit_va_id', '=', self.va_a.id),
            ('memo', '=', 'WH_VA_A_SUCCESS'),
        ], limit=1)
        self.assertTrue(payment_a)
        # Should be posted/in_process/paid (not draft)
        self.assertNotEqual(payment_a.state, 'draft')

        # 2. Company B has auto_post = False. Send VA payment for company B.
        response = self._post_webhook({
            'creditAccountNumber': '2222222222',
            'amount': 250.0,
            'reference': 'WH_VA_B_DRAFT',
            'gatewayCode': '00',
            'publicKey': 'KEY_B_PUB',
        })
        self.assertEqual(response.status_code, 200)
        payment_b = self.env['account.payment'].sudo().search([
            ('seerbit_va_id', '=', self.va_b.id),
            ('memo', '=', 'WH_VA_B_DRAFT'),
        ], limit=1)
        self.assertTrue(payment_b)
        self.assertEqual(payment_b.state, 'draft')

        # 3. Mismatched public key: webhook uses Key B for Company A's VA.
        before_count = self.env['account.payment'].sudo().search_count([])
        response = self._post_webhook({
            'creditAccountNumber': '1111111111',
            'amount': 100.0,
            'reference': 'WH_VA_A_MISMATCH',
            'gatewayCode': '00',
            'publicKey': 'KEY_B_PUB',  # Wrong public key for company A
        })
        self.assertEqual(response.status_code, 200)
        # No payment created
        after_count = self.env['account.payment'].sudo().search_count([])
        self.assertEqual(before_count, after_count)

    def test_webhook_payment_link_routing_and_autopost(self):
        """Test webhook processing of payment link, matching key & auto-post config."""
        # 1. Link A (Company A: auto_post = True)
        response = self._post_webhook({
            'paymentLinkId': 'LINK_ID_A',
            'amount': 300.0,
            'reference': 'WH_LINK_A_SUCCESS',
            'gatewayCode': '00',
            'publicKey': 'KEY_A_PUB',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.link_a.state, 'paid')
        payment_a = self.link_a.payment_id
        self.assertTrue(payment_a)
        self.assertNotEqual(payment_a.state, 'draft')

        # 2. Link B (Company B: auto_post = False)
        response = self._post_webhook({
            'paymentLinkId': 'LINK_ID_B',
            'amount': 400.0,
            'reference': 'WH_LINK_B_DRAFT',
            'gatewayCode': '00',
            'publicKey': 'KEY_B_PUB',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.link_b.state, 'paid')
        payment_b = self.link_b.payment_id
        self.assertTrue(payment_b)
        self.assertEqual(payment_b.state, 'draft')

        # Reset state of link A to verify
        self.link_a.write({'state': 'pending', 'payment_id': False})
        response = self._post_webhook({
            'paymentLinkId': 'LINK_ID_A',
            'amount': 300.0,
            'reference': 'WH_LINK_A_MISMATCH',
            'gatewayCode': '00',
            'publicKey': 'KEY_B_PUB',  # Wrong key
        })
        self.assertEqual(response.status_code, 200)
        # Should remain pending, no payment assigned
        self.assertEqual(self.link_a.state, 'pending')
        self.assertFalse(self.link_a.payment_id)

# -*- coding: utf-8 -*-
from unittest.mock import MagicMock, patch

from odoo.tests.common import tagged

from .common import SeerbitCommon


@tagged('post_install', '-at_install', 'pos_seerbit')
class TestSeerbitPayout(SeerbitCommon):

    def test_payout_defaults_company(self):
        payout = self.env['seerbit.payout'].with_company(self.company_b).create({
            'amount': 50.0,
            'bank_name': 'Access Bank',
            'bank_code': '044',
            'account_number': '0123456789',
            'account_name': 'Vendor Test',
            'company_id': self.company_b.id,
        })
        self.assertEqual(payout.company_id, self.company_b)
        self.assertTrue(payout.name.startswith('PAYOUT-'))
        self.assertEqual(payout.state, 'draft')

    def test_create_odoo_payment_uses_company_journal(self):
        payout = self.env['seerbit.payout'].create({
            'amount': 75.0,
            'bank_name': 'Access Bank',
            'bank_code': '044',
            'account_number': '0123456789',
            'account_name': 'Vendor Test',
            'company_id': self.company_a.id,
            'partner_id': self.partner_a.id,
            'state': 'completed',
        })
        payout._create_odoo_payment()
        self.assertTrue(payout.payment_id)
        self.assertEqual(payout.payment_id.company_id, self.company_a)
        self.assertEqual(payout.payment_id.journal_id.company_id, self.company_a)

    def test_otp_and_submit_use_record_company(self):
        payout = self.env['seerbit.payout'].create({
            'amount': 40.0,
            'bank_name': 'GTBank',
            'bank_code': '058',
            'account_number': '0987654321',
            'account_name': 'Vendor B',
            'company_id': self.company_b.id,
        })
        companies_used = []

        class TrackingPocketAPI:
            def __init__(self, env, company=None):
                companies_used.append(company.id if company else None)

            def get_otp(self):
                return True

            def submit_payout(self, **kwargs):
                return {'status': 'SUCCESS'}

            def authenticate(self, *a, **k):
                return {'bearerToken': 'tok'}

        with patch(
            'odoo.addons.pos_seerbit.models.payout.SeerbitPocketAPI',
            TrackingPocketAPI,
        ), patch.object(
            type(payout), '_create_odoo_payment', lambda self: True,
        ):
            payout.action_get_otp()
            self.assertEqual(payout.state, 'pending_otp')
            payout.otp_code = '123456'
            payout.action_submit_payout()
            self.assertEqual(payout.state, 'completed')

        self.assertTrue(companies_used)
        self.assertTrue(all(cid == self.company_b.id for cid in companies_used))

    def test_authenticate_pocket_with_config(self):
        mock_api = MagicMock()
        mock_api.authenticate.return_value = {'bearerToken': 'x'}
        with patch(
            'odoo.addons.pos_seerbit.models.payout.SeerbitPocketAPI',
            return_value=mock_api,
        ):
            ok = self.env['seerbit.payout'].with_company(
                self.company_a
            ).authenticate_pocket_with_config()
        self.assertTrue(ok)
        mock_api.authenticate.assert_called_once()

# -*- coding: utf-8 -*-
from odoo.tests.common import tagged

from odoo.addons.pos_seerbit.hooks import (
    ensure_seerbit_journal,
    ensure_seerbit_payment_method,
    ensure_seerbit_setup_all_companies,
)

from .common import SeerbitCommon


@tagged('post_install', '-at_install', 'pos_seerbit')
class TestSeerbitDashboardAndHooks(SeerbitCommon):

    def test_dashboard_scoped_to_company(self):
        # Create a payment on company A journal
        payment = self.env['account.payment'].with_company(self.company_a).create({
            'payment_type': 'inbound',
            'partner_type': 'customer',
            'partner_id': self.partner_a.id,
            'amount': 111.0,
            'journal_id': self.journal_a.id,
            'company_id': self.company_a.id,
        })
        payment.action_post()

        data_a = self.env['seerbit.dashboard.backend'].with_company(
            self.company_a
        ).get_dashboard_data()
        self.assertEqual(data_a['journal_id'], self.journal_a.id)
        ids_a = {t['id'] for t in data_a['recent_transactions']}
        self.assertIn(payment.id, ids_a)

        data_b = self.env['seerbit.dashboard.backend'].with_company(
            self.company_b
        ).get_dashboard_data()
        self.assertEqual(data_b['journal_id'], self.journal_b.id)
        ids_b = {t['id'] for t in data_b['recent_transactions']}
        self.assertNotIn(payment.id, ids_b)

    def test_ensure_journal_per_company(self):
        company_c = self.env['res.company'].create({
            'name': 'Seerbit Hook Company C',
            'currency_id': self.company_a.currency_id.id,
        })
        journal = ensure_seerbit_journal(self.env, company_c)
        self.assertTrue(journal)
        self.assertEqual(journal.company_id, company_c)
        self.assertEqual(journal.code, 'SEER')
        # Idempotent
        journal2 = ensure_seerbit_journal(self.env, company_c)
        self.assertEqual(journal, journal2)

    def test_ensure_payment_method_per_company(self):
        journal = ensure_seerbit_journal(self.env, self.company_b)
        pm = ensure_seerbit_payment_method(self.env, self.company_b, journal)
        self.assertTrue(pm)
        self.assertEqual(pm.company_id, self.company_b)
        self.assertEqual(pm.use_payment_terminal, 'seerbit')

    def test_ensure_setup_all_companies(self):
        ensure_seerbit_setup_all_companies(self.env)
        for company in (self.company_a, self.company_b):
            journal = self.env['account.journal'].search([
                ('code', '=', 'SEER'),
                ('company_id', '=', company.id),
            ], limit=1)
            self.assertTrue(journal, f"Missing SEER journal for {company.name}")

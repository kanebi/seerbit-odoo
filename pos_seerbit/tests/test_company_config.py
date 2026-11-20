# -*- coding: utf-8 -*-
from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

from .common import SeerbitCommon


@tagged('post_install', '-at_install', 'pos_seerbit')
class TestSeerbitCompanyConfig(SeerbitCommon):

    def test_company_fields_present(self):
        co = self.company_a.sudo()
        self.assertEqual(co.seerbit_public_key, 'SB_PUB_TEST_A')
        self.assertEqual(co.seerbit_secret_key, 'SB_SEC_TEST_A')
        self.assertEqual(co.seerbit_pocket_id, 'SBP_A')
        self.assertTrue(co.seerbit_auto_post)
        self.assertTrue(co.seerbit_auto_reconcile)
        self.assertFalse(self.company_b.sudo().seerbit_auto_post)

    def test_public_key_unique_constraint(self):
        with self.assertRaises(ValidationError):
            self.company_b.sudo().write({'seerbit_public_key': 'SB_PUB_TEST_A'})

    def test_seerbit_company_helper(self):
        Company = self.env['res.company']
        self.assertEqual(Company.seerbit_company(self.company_b).id, self.company_b.id)
        self.assertEqual(Company.seerbit_company(self.company_b.id).id, self.company_b.id)
        self.assertEqual(Company.seerbit_company().id, self.env.company.id)

    def test_seerbit_company_by_public_key(self):
        Company = self.env['res.company']
        found = Company.seerbit_company_by_public_key('SB_PUB_TEST_B')
        self.assertEqual(found, self.company_b)
        self.assertFalse(Company.seerbit_company_by_public_key('UNKNOWN_KEY'))
        self.assertFalse(Company.seerbit_company_by_public_key(''))

    def test_pocket_bearer_param_key_per_company(self):
        self.assertEqual(
            self.company_a.seerbit_pocket_bearer_param_key(),
            f'pos_seerbit.pocket_bearer_token.{self.company_a.id}',
        )
        self.assertNotEqual(
            self.company_a.seerbit_pocket_bearer_param_key(),
            self.company_b.seerbit_pocket_bearer_param_key(),
        )

    def test_settings_related_fields(self):
        settings = self.env['res.config.settings'].with_company(self.company_a).create({
            'company_id': self.company_a.id,
        })
        self.assertEqual(settings.seerbit_public_key, 'SB_PUB_TEST_A')
        self.assertIn(self.company_a.name, settings.seerbit_business_config_title)

        settings.write({'seerbit_pocket_id': 'SBP_A_UPDATED'})
        self.assertEqual(self.company_a.sudo().seerbit_pocket_id, 'SBP_A_UPDATED')

    def test_pos_payment_method_computed_public_key(self):
        pm = self.env['pos.payment.method'].create({
            'name': 'Seerbit Test Terminal',
            'use_payment_terminal': 'seerbit',
            'company_id': self.company_a.id,
            'journal_id': self.journal_a.id,
            'seerbit_terminal_id': 'TERM_A_1',
        })
        self.assertEqual(pm.seerbit_public_key, 'SB_PUB_TEST_A')
        self.company_a.sudo().seerbit_public_key = 'SB_PUB_TEST_A2'
        # Recompute
        pm.invalidate_recordset(['seerbit_public_key'])
        self.assertEqual(pm.seerbit_public_key, 'SB_PUB_TEST_A2')

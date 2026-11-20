# -*- coding: utf-8 -*-
"""Backfill VA company_id from partner / related payments (multi-company aware)."""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID

    cr.execute("""
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'seerbit_virtual_account' AND column_name = 'company_id'
    """)
    if not cr.fetchone():
        _logger.warning("pos_seerbit 0.2.3: company_id column missing on seerbit_virtual_account; skip")
        return

    env = api.Environment(cr, SUPERUSER_ID, {})
    companies = env['res.company'].sudo().search([], order='id')
    if not companies:
        return
    default_id = companies.id if len(companies) == 1 else companies[0].id

    cr.execute("""
        UPDATE seerbit_virtual_account va
           SET company_id = p.company_id
          FROM res_partner p
         WHERE va.partner_id = p.id
           AND va.company_id IS NULL
           AND p.company_id IS NOT NULL
    """)
    cr.execute("""
        UPDATE seerbit_virtual_account va
           SET company_id = sub.company_id
          FROM (
                SELECT DISTINCT ON (seerbit_va_id) seerbit_va_id, company_id
                  FROM account_payment
                 WHERE seerbit_va_id IS NOT NULL AND company_id IS NOT NULL
                 ORDER BY seerbit_va_id, id
          ) sub
         WHERE va.id = sub.seerbit_va_id
           AND va.company_id IS NULL
    """)
    cr.execute("""
        UPDATE seerbit_virtual_account
           SET company_id = %s
         WHERE company_id IS NULL
    """, [default_id])
    _logger.info("pos_seerbit 0.2.3: backfilled seerbit_virtual_account.company_id")

# -*- coding: utf-8 -*-
"""Backfill company_id on payouts and payment links (multi-company aware)."""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID

    env = api.Environment(cr, SUPERUSER_ID, {})
    companies = env['res.company'].sudo().search([], order='id')
    if not companies:
        return
    default_id = companies.id if len(companies) == 1 else companies[0].id

    cr.execute("""
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'seerbit_payout' AND column_name = 'company_id'
    """)
    if cr.fetchone():
        cr.execute("""
            UPDATE seerbit_payout po
               SET company_id = m.company_id
              FROM account_move m
             WHERE po.move_id = m.id
               AND po.company_id IS NULL
               AND m.company_id IS NOT NULL
        """)
        cr.execute("""
            UPDATE seerbit_payout po
               SET company_id = p.company_id
              FROM res_partner p
             WHERE po.partner_id = p.id
               AND po.company_id IS NULL
               AND p.company_id IS NOT NULL
        """)
        cr.execute("""
            UPDATE seerbit_payout
               SET company_id = %s
             WHERE company_id IS NULL
        """, [default_id])
        _logger.info("pos_seerbit 0.2.4: backfilled seerbit_payout.company_id")

    cr.execute("""
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'pos_seerbit_payment_link' AND column_name = 'company_id'
    """)
    if cr.fetchone():
        cr.execute("""
            UPDATE pos_seerbit_payment_link pl
               SET company_id = m.company_id
              FROM account_move m
             WHERE pl.move_id = m.id
               AND pl.company_id IS NULL
               AND m.company_id IS NOT NULL
        """)
        cr.execute("""
            UPDATE pos_seerbit_payment_link pl
               SET company_id = p.company_id
              FROM res_partner p
             WHERE pl.partner_id = p.id
               AND pl.company_id IS NULL
               AND p.company_id IS NOT NULL
        """)
        cr.execute("""
            UPDATE pos_seerbit_payment_link pl
               SET company_id = sub.company_id
              FROM (
                    SELECT DISTINCT ON (seerbit_payment_link_id)
                           seerbit_payment_link_id, company_id
                      FROM account_payment
                     WHERE seerbit_payment_link_id IS NOT NULL AND company_id IS NOT NULL
                     ORDER BY seerbit_payment_link_id, id
              ) sub
             WHERE pl.id = sub.seerbit_payment_link_id
               AND pl.company_id IS NULL
        """)
        cr.execute("""
            UPDATE pos_seerbit_payment_link
               SET company_id = %s
             WHERE company_id IS NULL
        """, [default_id])
        _logger.info("pos_seerbit 0.2.4: backfilled pos_seerbit_payment_link.company_id")

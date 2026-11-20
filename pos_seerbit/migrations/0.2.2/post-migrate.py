# -*- coding: utf-8 -*-
"""
Move global Seerbit pocket / auto-post ICP values onto companies.

Multi-company (where technically possible):
- Auto post / auto reconcile → every company (shared behavioral defaults).
- Pocket id/email/password → every Seerbit-active company still empty
  (had a Seerbit PM, or already has a public key). Historically one merchant
  pocket was used system-wide; branches can override later in Settings.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID

    cr.execute("""
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'res_company' AND column_name = 'seerbit_pocket_id'
    """)
    if not cr.fetchone():
        _logger.warning("pos_seerbit 0.2.2: company business columns missing; skip")
        return

    env = api.Environment(cr, SUPERUSER_ID, {})
    ICP = env['ir.config_parameter'].sudo()
    Company = env['res.company'].sudo()

    companies = Company.search([], order='id')
    if not companies:
        return

    legacy = {
        'seerbit_pocket_id': (ICP.get_param('pos_seerbit.seerbit_pocket_id') or '').strip(),
        'seerbit_pocket_email': (ICP.get_param('pos_seerbit.seerbit_pocket_email') or '').strip(),
        'seerbit_pocket_password': ICP.get_param('pos_seerbit.seerbit_pocket_password') or '',
        'seerbit_auto_post': ICP.get_param('pos_seerbit.seerbit_auto_post', 'True') == 'True',
        'seerbit_auto_reconcile': ICP.get_param('pos_seerbit.seerbit_auto_reconcile', 'True') == 'True',
    }

    cr.execute("""
        SELECT DISTINCT company_id
          FROM pos_payment_method
         WHERE use_payment_terminal = 'seerbit'
           AND company_id IS NOT NULL
    """)
    seerbit_pm_company_ids = {row[0] for row in cr.fetchall()}

    # If only one company, treat it as Seerbit-active even without a PM yet.
    single = len(companies) == 1

    for company in companies:
        has_pub = bool((company.seerbit_public_key or '').strip())
        seerbit_active = single or has_pub or company.id in seerbit_pm_company_ids

        vals = {}
        # Behavioral flags: apply to all companies
        if legacy['seerbit_auto_post'] is False and company.seerbit_auto_post:
            vals['seerbit_auto_post'] = False
        if legacy['seerbit_auto_reconcile'] is False and company.seerbit_auto_reconcile:
            vals['seerbit_auto_reconcile'] = False

        # Pocket credentials: only onto Seerbit-active companies still empty
        if seerbit_active:
            if not (company.seerbit_pocket_id or '').strip() and legacy['seerbit_pocket_id']:
                vals['seerbit_pocket_id'] = legacy['seerbit_pocket_id']
            if not (company.seerbit_pocket_email or '').strip() and legacy['seerbit_pocket_email']:
                vals['seerbit_pocket_email'] = legacy['seerbit_pocket_email']
            if not (company.seerbit_pocket_password or '') and legacy['seerbit_pocket_password']:
                vals['seerbit_pocket_password'] = legacy['seerbit_pocket_password']

        if vals:
            company.write(vals)
            _logger.info("pos_seerbit 0.2.2: seeded business config on %s (%s)", company.name, list(vals))

# -*- coding: utf-8 -*-
"""
Move Seerbit pub/secret keys from global ICP / payment methods → res.company.

Multi-company:
- Payment-method public keys → that PM's company (always safe, per branch).
- Global public key → every company that still has no key AND would not create a
  duplicate (so at most one company receives a shared global pub key).
- Global secret → every company that has (or just received) a public key, or that
  has a Seerbit POS payment method (secret is not unique-constrained).
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID

    cr.execute("""
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'res_company' AND column_name = 'seerbit_public_key'
    """)
    if not cr.fetchone():
        _logger.warning("pos_seerbit 0.2.0: res.company Seerbit columns missing; skip key migration")
        return

    env = api.Environment(cr, SUPERUSER_ID, {})
    Company = env['res.company'].sudo()
    ICP = env['ir.config_parameter'].sudo()

    companies = Company.search([], order='id')
    if not companies:
        return

    global_pub = (ICP.get_param('pos_seerbit.seerbit_public_key') or '').strip()
    global_secret = (ICP.get_param('pos_seerbit.seerbit_secret_key') or '').strip()

    # Per-company keys from stored PM column (pre-computed-field DBs)
    pm_keys = {}
    seerbit_pm_company_ids = set()
    cr.execute("""
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'pos_payment_method' AND column_name = 'seerbit_public_key'
    """)
    has_pm_key_col = bool(cr.fetchone())

    if has_pm_key_col:
        cr.execute("""
            SELECT company_id, seerbit_public_key
              FROM pos_payment_method
             WHERE use_payment_terminal = 'seerbit'
               AND company_id IS NOT NULL
             ORDER BY id
        """)
        for company_id, pub_key in cr.fetchall():
            seerbit_pm_company_ids.add(company_id)
            key = (pub_key or '').strip()
            if key and company_id not in pm_keys:
                pm_keys[company_id] = key
    else:
        cr.execute("""
            SELECT DISTINCT company_id
              FROM pos_payment_method
             WHERE use_payment_terminal = 'seerbit'
               AND company_id IS NOT NULL
        """)
        seerbit_pm_company_ids = {row[0] for row in cr.fetchall()}

    # Track keys already claimed so we never create duplicates from global seed
    claimed_keys = {
        (c.seerbit_public_key or '').strip()
        for c in companies
        if (c.seerbit_public_key or '').strip()
    }

    for company in companies:
        pub = (company.seerbit_public_key or '').strip()
        new_pub = None

        if not pub and pm_keys.get(company.id):
            candidate = pm_keys[company.id]
            if candidate not in claimed_keys:
                new_pub = candidate
            elif candidate == pub:
                pass
            else:
                # Another company already owns this key; skip (0.2.6 will dedupe leftovers)
                _logger.warning(
                    "pos_seerbit 0.2.0: PM key for %s already claimed by another company; skip",
                    company.name,
                )

        if not pub and not new_pub and global_pub and global_pub not in claimed_keys:
            # Assign global pub to this company only if still unclaimed
            new_pub = global_pub

        if new_pub:
            cr.execute(
                "UPDATE res_company SET seerbit_public_key = %s WHERE id = %s",
                [new_pub, company.id],
            )
            claimed_keys.add(new_pub)
            pub = new_pub
            _logger.info("pos_seerbit 0.2.0: set seerbit_public_key on %s", company.name)

        # Secret: apply to every Seerbit-active company still missing one
        needs_secret = not (company.seerbit_secret_key or '').strip()
        is_seerbit_co = bool(pub) or company.id in seerbit_pm_company_ids or bool(new_pub)
        if needs_secret and global_secret and is_seerbit_co:
            cr.execute(
                "UPDATE res_company SET seerbit_secret_key = %s WHERE id = %s",
                [global_secret, company.id],
            )
            _logger.info("pos_seerbit 0.2.0: set seerbit_secret_key on %s", company.name)

    # If global pub was never claimed (all companies already had distinct keys), nothing else to do.
    # If some companies have Seerbit PMs but no key left, log for admin.
    for company in companies:
        company.invalidate_recordset(['seerbit_public_key'])
        if company.id in seerbit_pm_company_ids and not (company.seerbit_public_key or '').strip():
            _logger.warning(
                "pos_seerbit 0.2.0: %s has a Seerbit POS method but no public key — "
                "set it in Settings → Seerbit.",
                company.name,
            )

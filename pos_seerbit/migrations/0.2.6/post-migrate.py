# -*- coding: utf-8 -*-
"""
Consolidate per-company Seerbit settings for existing databases (multi-company aware).

1. Copy leftover stored payment-method public keys → matching company
2. Deduplicate shared seerbit_public_key (keep on best company, clear others)
3. Seed remaining legacy ICP onto every Seerbit-active company where safe
4. Migrate global pocket bearer / encrypted key ICP → each Seerbit-active company
5. Backfill company_id on VA / payout / payment link from related records
6. Remove obsolete global ICP parameters that moved to res.company
"""
import logging

_logger = logging.getLogger(__name__)

_OBSOLETE_ICP_KEYS = (
    'pos_seerbit.seerbit_public_key',
    'pos_seerbit.seerbit_secret_key',
    'pos_seerbit.seerbit_pocket_id',
    'pos_seerbit.seerbit_pocket_email',
    'pos_seerbit.seerbit_pocket_password',
    'pos_seerbit.seerbit_auto_post',
    'pos_seerbit.seerbit_auto_reconcile',
    'pos_seerbit.pocket_bearer_token',
    'pos_seerbit.seerbit_encrypted_key',
)


def _column_exists(cr, table, column):
    cr.execute("""
        SELECT 1 FROM information_schema.columns
         WHERE table_name = %s AND column_name = %s
    """, [table, column])
    return bool(cr.fetchone())


def _seerbit_company_ids(cr, companies):
    """Companies that look Seerbit-active (PM and/or public key)."""
    cr.execute("""
        SELECT DISTINCT company_id
          FROM pos_payment_method
         WHERE use_payment_terminal = 'seerbit'
           AND company_id IS NOT NULL
    """)
    ids = {row[0] for row in cr.fetchall()}
    for company in companies:
        if (company.seerbit_public_key or '').strip():
            ids.add(company.id)
    if len(companies) == 1:
        ids.add(companies.id)
    return ids


def _copy_pm_keys_to_companies(cr):
    if not _column_exists(cr, 'pos_payment_method', 'seerbit_public_key'):
        return

    cr.execute("""
        SELECT company_id, seerbit_public_key
          FROM pos_payment_method
         WHERE use_payment_terminal = 'seerbit'
           AND seerbit_public_key IS NOT NULL
           AND TRIM(seerbit_public_key) != ''
           AND company_id IS NOT NULL
         ORDER BY id
    """)
    pm_rows = cr.fetchall()

    for company_id, pub_key in pm_rows:
        key = (pub_key or '').strip()
        if not key:
            continue
        # Skip if this key is already on another company
        cr.execute("""
            SELECT id FROM res_company
             WHERE TRIM(seerbit_public_key) = %s AND id != %s
             LIMIT 1
        """, [key, company_id])
        if cr.fetchone():
            continue
        cr.execute("""
            UPDATE res_company
               SET seerbit_public_key = %s
             WHERE id = %s
               AND (seerbit_public_key IS NULL OR TRIM(seerbit_public_key) = '')
        """, [key, company_id])
        if cr.rowcount:
            _logger.info(
                "pos_seerbit 0.2.6: copied PM public key onto company id=%s", company_id
            )


def _dedupe_public_keys(cr, companies):
    """Keep each shared public key on one company; clear others (SQL, bypasses constraint)."""
    if not _column_exists(cr, 'res_company', 'seerbit_public_key'):
        return

    cr.execute("""
        SELECT seerbit_public_key, array_agg(id ORDER BY id)
          FROM res_company
         WHERE seerbit_public_key IS NOT NULL
           AND TRIM(seerbit_public_key) != ''
         GROUP BY seerbit_public_key
        HAVING count(*) > 1
    """)
    duplicates = cr.fetchall()
    if not duplicates:
        return

    cr.execute("""
        SELECT DISTINCT company_id
          FROM pos_payment_method
         WHERE use_payment_terminal = 'seerbit'
           AND company_id IS NOT NULL
    """)
    pm_company_ids = {row[0] for row in cr.fetchall()}

    # Prefer company that has invoices/VAs/links already using Seerbit under that co
    for key, company_ids in duplicates:
        ids = list(company_ids or [])
        keeper = None
        for cid in ids:
            if cid in pm_company_ids:
                keeper = cid
                break
        if keeper is None:
            # Prefer company with the most related Seerbit documents
            best_score, best_id = -1, ids[0]
            for cid in ids:
                score = 0
                if _column_exists(cr, 'seerbit_virtual_account', 'company_id'):
                    cr.execute(
                        "SELECT COUNT(*) FROM seerbit_virtual_account WHERE company_id = %s",
                        [cid],
                    )
                    score += cr.fetchone()[0]
                if _column_exists(cr, 'pos_seerbit_payment_link', 'company_id'):
                    cr.execute(
                        "SELECT COUNT(*) FROM pos_seerbit_payment_link WHERE company_id = %s",
                        [cid],
                    )
                    score += cr.fetchone()[0]
                cr.execute(
                    "SELECT COUNT(*) FROM account_move WHERE company_id = %s AND synced_with_seerbit IS TRUE",
                    [cid],
                )
                score += cr.fetchone()[0]
                if score > best_score:
                    best_score, best_id = score, cid
            keeper = best_id

        clear_ids = [cid for cid in ids if cid != keeper]
        cr.execute(
            "UPDATE res_company SET seerbit_public_key = NULL WHERE id = ANY(%s)",
            [clear_ids],
        )
        _logger.warning(
            "pos_seerbit 0.2.6: public key shared; kept on %s, cleared from [%s]. "
            "Set a distinct Public Key per cleared company in Settings → Seerbit.",
            companies.browse(keeper).name,
            ', '.join(companies.browse(clear_ids).mapped('name')),
        )


def _seed_remaining_icp(env, companies, seerbit_ids):
    """Apply leftover global ICP to every Seerbit-active company where still empty / safe."""
    ICP = env['ir.config_parameter'].sudo()
    cr = env.cr

    pub = (ICP.get_param('pos_seerbit.seerbit_public_key') or '').strip()
    secret = (ICP.get_param('pos_seerbit.seerbit_secret_key') or '').strip()
    pocket_id = (ICP.get_param('pos_seerbit.seerbit_pocket_id') or '').strip()
    pocket_email = (ICP.get_param('pos_seerbit.seerbit_pocket_email') or '').strip()
    pocket_password = ICP.get_param('pos_seerbit.seerbit_pocket_password') or ''
    auto_post = ICP.get_param('pos_seerbit.seerbit_auto_post')
    auto_rec = ICP.get_param('pos_seerbit.seerbit_auto_reconcile')

    # Who already owns the global pub key?
    pub_owner = None
    if pub:
        cr.execute(
            "SELECT id FROM res_company WHERE TRIM(seerbit_public_key) = %s LIMIT 1",
            [pub],
        )
        row = cr.fetchone()
        pub_owner = row[0] if row else None

    for company in companies:
        vals = {}
        active = company.id in seerbit_ids

        # Public key: only if unclaimed and company still empty
        if active and pub and not (company.seerbit_public_key or '').strip():
            if pub_owner is None:
                cr.execute(
                    "UPDATE res_company SET seerbit_public_key = %s WHERE id = %s",
                    [pub, company.id],
                )
                pub_owner = company.id
                _logger.info("pos_seerbit 0.2.6: seeded public key onto %s", company.name)
            # else already owned — leave empty for admin to set a distinct key

        if active and secret and not (company.seerbit_secret_key or '').strip():
            vals['seerbit_secret_key'] = secret
        if active and pocket_id and not (company.seerbit_pocket_id or '').strip():
            vals['seerbit_pocket_id'] = pocket_id
        if active and pocket_email and not (company.seerbit_pocket_email or '').strip():
            vals['seerbit_pocket_email'] = pocket_email
        if active and pocket_password and not (company.seerbit_pocket_password or ''):
            vals['seerbit_pocket_password'] = pocket_password

        # Auto flags: all companies
        if auto_post == 'False' and company.seerbit_auto_post:
            vals['seerbit_auto_post'] = False
        if auto_rec == 'False' and company.seerbit_auto_reconcile:
            vals['seerbit_auto_reconcile'] = False

        if vals:
            company.write(vals)
            _logger.info("pos_seerbit 0.2.6: seeded ICP fields on %s (%s)", company.name, list(vals))


def _migrate_token_icp(env, seerbit_ids):
    """Copy global bearer/encrypted tokens onto every Seerbit-active company missing them."""
    ICP = env['ir.config_parameter'].sudo()

    legacy_bearer = ICP.get_param('pos_seerbit.pocket_bearer_token')
    legacy_enc = ICP.get_param('pos_seerbit.seerbit_encrypted_key')

    for company_id in seerbit_ids:
        if legacy_bearer:
            key = f'pos_seerbit.pocket_bearer_token.{company_id}'
            if not ICP.get_param(key):
                ICP.set_param(key, legacy_bearer)
                _logger.info("pos_seerbit 0.2.6: pocket bearer → %s", key)
        if legacy_enc:
            key = f'pos_seerbit.seerbit_encrypted_key.{company_id}'
            if not ICP.get_param(key):
                ICP.set_param(key, legacy_enc)
                _logger.info("pos_seerbit 0.2.6: encrypted key → %s", key)


def _backfill_company_id(cr, companies):
    """
    Fill NULL company_id from related documents.
    Single-company → that company. Multi → invoice/partner first; then company of
    existing Seerbit payments; finally leave NULL only if no signal (then use main).
    """
    main_id = companies[0].id
    if len(companies) == 1:
        default_id = companies.id
    else:
        default_id = main_id

    # Virtual accounts
    if _column_exists(cr, 'seerbit_virtual_account', 'company_id'):
        # 1) partner.company_id
        cr.execute("""
            UPDATE seerbit_virtual_account va
               SET company_id = p.company_id
              FROM res_partner p
             WHERE va.partner_id = p.id
               AND va.company_id IS NULL
               AND p.company_id IS NOT NULL
        """)
        # 2) company of an existing payment on this VA
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
        # 3) fallback default
        cr.execute("""
            UPDATE seerbit_virtual_account
               SET company_id = %s
             WHERE company_id IS NULL
        """, [default_id])
        _logger.info("pos_seerbit 0.2.6: VA company_id backfill done")

    # Payouts
    if _column_exists(cr, 'seerbit_payout', 'company_id'):
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
        _logger.info("pos_seerbit 0.2.6: payout company_id backfill done")

    # Payment links
    if _column_exists(cr, 'pos_seerbit_payment_link', 'company_id'):
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
        _logger.info("pos_seerbit 0.2.6: payment link company_id backfill done")


def _cleanup_obsolete_icp(env):
    ICP = env['ir.config_parameter'].sudo()
    for key in _OBSOLETE_ICP_KEYS:
        param = ICP.search([('key', '=', key)], limit=1)
        if param:
            param.unlink()
            _logger.info("pos_seerbit 0.2.6: removed obsolete ICP %s", key)


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID

    if not _column_exists(cr, 'res_company', 'seerbit_public_key'):
        _logger.warning("pos_seerbit 0.2.6: company Seerbit columns missing; skip")
        return

    env = api.Environment(cr, SUPERUSER_ID, {})
    companies = env['res.company'].sudo().search([], order='id')
    if not companies:
        return

    _logger.info(
        "pos_seerbit 0.2.6: consolidating Seerbit settings for %s companies",
        len(companies),
    )

    _copy_pm_keys_to_companies(cr)
    companies.invalidate_recordset()
    _dedupe_public_keys(cr, companies)
    companies.invalidate_recordset()

    seerbit_ids = _seerbit_company_ids(cr, companies)
    _seed_remaining_icp(env, companies, seerbit_ids)
    _migrate_token_icp(env, seerbit_ids)
    _backfill_company_id(cr, companies)
    _cleanup_obsolete_icp(env)

    cr.execute("""
        SELECT seerbit_public_key, array_agg(name ORDER BY id), count(*)
          FROM res_company
         WHERE seerbit_public_key IS NOT NULL
           AND TRIM(seerbit_public_key) != ''
         GROUP BY seerbit_public_key
        HAVING count(*) > 1
    """)
    for key, names, count in cr.fetchall():
        _logger.error(
            "pos_seerbit 0.2.6: public key %s still shared by %s companies (%s)",
            key, count, ', '.join(names or []),
        )

    _logger.info("pos_seerbit 0.2.6: consolidation complete")

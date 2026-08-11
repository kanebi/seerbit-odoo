from odoo import SUPERUSER_ID
import base64
import os
import logging

_logger = logging.getLogger(__name__)


def _load_seerbit_logo(env):
    logo_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), 'static/description/seerbit_logo.png')
    )
    if not os.path.exists(logo_path):
        _logger.warning("Logo file not found at: %s", logo_path)
        return False
    try:
        with open(logo_path, 'rb') as f:
            return base64.b64encode(f.read())
    except Exception as e:
        _logger.warning("Failed to read payment method logo: %s", e)
        return False


def _seerbit_location_label(env, company):
    """Derive a short location label for the Seerbit journal name."""
    COMPANY_LABELS = {
        'labule_core.company_labule_hq': 'Ogudu',
        'labule_core.company_labule_ikeja': 'Ikeja',
        'labule_core.company_labule_lekki': 'Lekki',
        'labule_core.company_labule_ikorodu': 'Ikorodu',
        'labule_core.company_labule_corporate': 'Corporate',
    }
    for xmlid, label in COMPANY_LABELS.items():
        ref = env.ref(xmlid, raise_if_not_found=False)
        if ref and ref.id == company.id:
            return label
    return company.name


def _rename_seerbit_default_account(journal, account_name):
    """Keep the journal's linked liquidity account aligned with the location name."""
    account = journal.default_account_id
    if not account:
        return
    if account.name != account_name:
        account.write({'name': account_name})


def ensure_seerbit_journal(env, company):
    """Ensure a Seerbit bank journal exists for the given company. Returns the journal."""
    currency = company.currency_id or env.ref('base.USD', raise_if_not_found=False)
    if not currency:
        _logger.warning("No currency for company %s; skip Seerbit journal", company.name)
        return env['account.journal']

    location = _seerbit_location_label(env, company)
    journal_name = f'Seerbit ({location})'
    account_name = journal_name

    journal = env['account.journal'].search([
        ('code', '=', 'SEER'),
        ('company_id', '=', company.id),
    ], limit=1)
    if not journal:
        # Prefer renaming an existing "Seerbit" bank journal if code differs
        journal = env['account.journal'].search([
            ('type', '=', 'bank'),
            ('name', 'ilike', 'Seerbit'),
            ('company_id', '=', company.id),
        ], limit=1)

    if not journal:
        journal = env['account.journal'].create({
            'name': journal_name,
            'type': 'bank',
            'code': 'SEER',
            'currency_id': currency.id,
            'company_id': company.id,
            'show_on_dashboard': True,
            'active': True,
        })
        _rename_seerbit_default_account(journal, account_name)
        _logger.info("Created Seerbit journal for %s (id=%s)", company.name, journal.id)
    else:
        update_values = {}
        if journal.name != journal_name:
            update_values['name'] = journal_name
        if journal.code != 'SEER' and not env['account.journal'].search([
            ('code', '=', 'SEER'),
            ('company_id', '=', company.id),
            ('id', '!=', journal.id),
        ], limit=1):
            update_values['code'] = 'SEER'
        if journal.currency_id != currency:
            update_values['currency_id'] = currency.id
        if not journal.show_on_dashboard:
            update_values['show_on_dashboard'] = True
        if not journal.active:
            update_values['active'] = True
        if update_values:
            journal.write(update_values)
            _logger.info("Updated Seerbit journal for %s: %s", company.name, update_values)
        _rename_seerbit_default_account(journal, account_name)

    return journal


def ensure_seerbit_payment_method(env, company, journal, image_data=None):
    """Ensure a Seerbit POS payment method exists for the company."""
    PaymentMethod = env['pos.payment.method']
    payment_method = PaymentMethod.search([
        ('use_payment_terminal', '=', 'seerbit'),
        ('company_id', '=', company.id),
    ], limit=1)

    # First company / XML record: reuse xmlid if it has no company or matches
    if not payment_method:
        xml_pm = env.ref('pos_seerbit.seerbit_pos', raise_if_not_found=False)
        if xml_pm and (not xml_pm.company_id or xml_pm.company_id == company):
            payment_method = xml_pm

    if not payment_method:
        payment_method = PaymentMethod.create({
            'name': 'Seerbit POS',
            'use_payment_terminal': 'seerbit',
            'is_cash_count': False,
            'company_id': company.id,
            'journal_id': journal.id if journal else False,
        })
        _logger.info("Created Seerbit POS payment method for %s (id=%s)", company.name, payment_method.id)

    receivable_account = journal.default_account_id if journal else False
    if not receivable_account:
        Account = env['account.account']
        domain = [
            ('account_type', '=', 'asset_receivable'),
            ('deprecated', '=', False),
        ]
        # Odoo 18 uses company_ids; older DBs may still have company_id
        if 'company_ids' in Account._fields:
            domain.append(('company_ids', 'in', [company.id]))
        elif 'company_id' in Account._fields:
            domain.append(('company_id', '=', company.id))
        receivable_account = Account.search(domain, limit=1)

    update_values = {}
    if journal and payment_method.journal_id != journal and journal.exists():
        update_values['journal_id'] = journal.id
    if payment_method.use_payment_terminal != 'seerbit':
        update_values['use_payment_terminal'] = 'seerbit'
    if not payment_method.receivable_account_id and receivable_account:
        update_values['receivable_account_id'] = receivable_account.id
    if payment_method.is_cash_count:
        update_values['is_cash_count'] = False
    if payment_method.company_id != company:
        update_values['company_id'] = company.id
    image_field = 'image' if 'image' in PaymentMethod._fields else 'image_128'
    if image_data and not payment_method[image_field]:
        update_values[image_field] = image_data

    if update_values:
        try:
            payment_method.write(update_values)
            _logger.info("Updated Seerbit payment method for %s: %s", company.name, list(update_values))
        except Exception as e:
            _logger.warning("Failed to update payment method for %s: %s", company.name, e)
            if 'journal_id' in update_values:
                update_values.pop('journal_id')
                if update_values:
                    try:
                        payment_method.write(update_values)
                    except Exception as e2:
                        _logger.error(
                            "Failed to update payment method for %s even without journal: %s",
                            company.name, e2,
                        )

    return payment_method


def ensure_seerbit_setup_all_companies(env):
    """Provision Seerbit journal + POS payment method for every company."""
    image_data = _load_seerbit_logo(env)
    companies = env['res.company'].sudo().search([])
    if not companies:
        _logger.warning("No company found, skipping Seerbit setup")
        return

    for company in companies:
        try:
            journal = ensure_seerbit_journal(env, company)
            if journal:
                ensure_seerbit_payment_method(env, company, journal, image_data=image_data)
        except Exception as e:
            _logger.error("Seerbit setup failed for company %s: %s", company.name, e)


def post_init_hook(env):
    """
    Ensure Seerbit journal and payment method exist for every company after install.
    """
    try:
        # Prefer sudo / admin env for setup writes
        if hasattr(env, 'user') and env.uid != SUPERUSER_ID:
            env = env(user=SUPERUSER_ID)
        ensure_seerbit_setup_all_companies(env)
        _logger.info("Seerbit setup completed for all companies")
    except Exception as e:
        _logger.error("Error during Seerbit setup: %s", str(e))
        # Don't raise — module can still work; journals can be created later via migration/upgrade

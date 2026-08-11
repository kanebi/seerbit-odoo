from odoo import api
import base64
import os
import logging
from odoo import SUPERUSER_ID

_logger = logging.getLogger(__name__)

def post_init_hook(env):
    """
    Safely ensure the Seerbit journal and payment method exist after module installation.
    Uses the SUPERUSER_ID's default company, or falls back to the first available company.
    """
    try:
        # Get the superuser (admin)
        user = env['res.users'].browse(SUPERUSER_ID)
        
        # Use superuser's default company, or fall back to any available company
        company = user.company_id or env['res.company'].search([], limit=1)
        
        currency = company.currency_id
        _logger.info(f"Company: {company.name}, Currency: {currency.name}")
        if not company:
            _logger.warning("No company found, skipping Seerbit setup")
            return
        if not currency:
            currency = env.ref('base.USD', raise_if_not_found=False)
            if not currency:
                _logger.warning("No currency found, skipping Seerbit setup")
                return

        # Safely handle the Seerbit journal
        journal = env['account.journal'].search([('code', '=', 'SEER'), ('company_id', '=', company.id)], limit=1)
        if not journal:
            # Create new journal
            journal = env['account.journal'].create({
                'name': 'Seerbit',
                'type': 'bank',
                'code': 'SEER',
                'currency_id': currency.id,
                'company_id': company.id,
                'show_on_dashboard': True,
                'active': True,
            })
            _logger.info("Created Seerbit journal with ID: %s", journal.id)
        else:
            # Update existing journal if needed
            update_values = {}
            if journal.name != 'Seerbit':
                update_values['name'] = 'Seerbit'
            if journal.currency_id != currency:
                update_values['currency_id'] = currency.id
            if not journal.show_on_dashboard:
                update_values['show_on_dashboard'] = True
            if not journal.active:
                update_values['active'] = True
                
            if update_values:
                journal.write(update_values)
                _logger.info("Updated Seerbit journal with values: %s", update_values)

        # Handle the Seerbit payment method - try to get the XML record first
        payment_method = env.ref('pos_seerbit.seerbit_pos', raise_if_not_found=False)
        if not payment_method:
            # Fallback to search by name if XML record doesn't exist
            payment_method = env['pos.payment.method'].search([('name', '=', 'Seerbit POS')], limit=1)
        
        if payment_method:
            # Update existing payment method
            receivable_account = journal.default_account_id
            if not receivable_account:
                # Try to find a suitable receivable account
                receivable_account = env['account.account'].search([
                    ('account_type', '=', 'asset_receivable'),
                    ('company_id', '=', company.id),
                    ('deprecated', '=', False)
                ], limit=1)
            
            update_values = {}
            # Only update journal if it's different and the new journal exists
            if payment_method.journal_id != journal and journal.exists():
                update_values['journal_id'] = journal.id
            if payment_method.use_payment_terminal != 'seerbit':
                update_values['use_payment_terminal'] = 'seerbit'
            if not payment_method.receivable_account_id and receivable_account:
                update_values['receivable_account_id'] = receivable_account.id
            if payment_method.is_cash_count:
                update_values['is_cash_count'] = False
            if payment_method.company_id != company:
                update_values['company_id'] = company.id
            
            if update_values:
                try:
                    payment_method.write(update_values)
                    _logger.info("Updated Seerbit payment method with values: %s", update_values)
                except Exception as e:
                    _logger.warning("Failed to update payment method: %s", str(e))
                    # Try updating without the journal change
                    if 'journal_id' in update_values:
                        update_values.pop('journal_id')
                        if update_values:
                            try:
                                payment_method.write(update_values)
                                _logger.info("Updated Seerbit payment method (without journal): %s", update_values)
                            except Exception as e2:
                                _logger.error("Failed to update payment method even without journal: %s", str(e2))
            else:
                _logger.info("Seerbit payment method already properly configured")
        else:
            _logger.warning("No Seerbit payment method found to configure")
        
        # Set image for Seerbit payment method if it exists
        if payment_method:
            logo_path = os.path.join(os.path.dirname(__file__), 'static/description/seerbit_logo.png')
            logo_path = os.path.abspath(logo_path)
            if os.path.exists(logo_path):
                try:
                    with open(logo_path, 'rb') as f:
                        image_data = base64.b64encode(f.read())
                        payment_method.write({'image_128': image_data})
                    _logger.info("Set Seerbit payment method logo")
                except Exception as e:
                    _logger.warning("Failed to set payment method logo: %s", str(e))
            else:
                _logger.warning("Logo file not found at: %s", logo_path)
                
        _logger.info("Seerbit setup completed successfully")
        
    except Exception as e:
        _logger.error("Error during Seerbit setup: %s", str(e))
        # Don't raise the exception to prevent module installation failure
        # The module can still work without the journal/payment method 
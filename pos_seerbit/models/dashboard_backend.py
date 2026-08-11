from odoo import api, fields, models
import logging
from ..services.seerbit_pocket_api import require_seerbit_auth

_logger = logging.getLogger(__name__)

class SeerbitDashboardBackend(models.AbstractModel):
    _name = 'seerbit.dashboard.backend'
    _description = 'Seerbit Dashboard Backend'

    @api.model
    def get_dashboard_data(self, filter_24h=False):
        journal = self.env['account.journal'].search([('type', '=', 'bank'), ('name', 'ilike', 'Seerbit')], limit=1)
        if not journal:
            journal = self.env['account.journal'].search([('type', '=', 'bank')], limit=1)
            
        odoo_balance = 0.0
        currency_symbol = self.env.company.currency_id.symbol
        if journal:
            account = journal.default_account_id
            if account:
                if filter_24h:
                    from datetime import timedelta
                    twenty_four_hours_ago = fields.Datetime.now() - timedelta(hours=24)
                    self.env.cr.execute("""
                        SELECT SUM(balance)
                        FROM account_move_line
                        WHERE account_id = %s AND parent_state = 'posted' AND create_date >= %s
                    """, [account.id, twenty_four_hours_ago])
                else:
                    self.env.cr.execute("""
                        SELECT SUM(balance)
                        FROM account_move_line
                        WHERE account_id = %s AND parent_state = 'posted'
                    """, [account.id])
                result = self.env.cr.fetchone()
                odoo_balance = result[0] if result and result[0] else 0.0
                
        domain = [('journal_id', '=', journal.id)] if journal else []
        if filter_24h:
            from datetime import timedelta
            twenty_four_hours_ago = fields.Datetime.now() - timedelta(hours=24)
            domain.append(('create_date', '>=', twenty_four_hours_ago))
        limit = None if filter_24h else 10
        payments = self.env['account.payment'].search(domain, limit=limit, order='date desc, id desc')
        
        recent_transactions = []
        for p in payments:
            recent_transactions.append({
                'id': p.id,
                'date': p.date.strftime('%Y-%m-%d'),
                'name': p.name,
                'memo': p.memo or '',
                'amount': p.amount,
                'payment_type': p.payment_type,
                'state': p.state,
                'is_reconciled': p.is_reconciled,
                'partner': p.partner_id.name if p.partner_id else '',
            })

        return {
            'odoo_balance': odoo_balance,
            'platform_balance': 0.0,
            'difference': 0.0,
            'needs_reconciliation': False,
            'currency_symbol': currency_symbol,
            'journal_id': journal.id if journal else False,
            'recent_transactions': recent_transactions,
        }

    @api.model
    @require_seerbit_auth
    def get_pocket_balance(self):
        from ..services.seerbit_pocket_api import SeerbitPocketAPI
        api_client = SeerbitPocketAPI(self.env)
        try:
            return float(api_client.get_pocket_balance())
        except Exception as e:
            if "POCKET_AUTH_REQUIRED" in str(e):
                raise
            _logger.warning(f"Failed to fetch platform balance: {e}")
            return 0.0

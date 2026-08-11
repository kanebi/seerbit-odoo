import sys
sys.path.append('/home/kane/odoo-18/odoo-source')
import odoo
from odoo import tools
tools.config.parse_config(['-c', '/home/kane/odoo-18/odoo.conf'])
db_name = tools.config['db_name'] or 'dev18'
registry = odoo.registry(db_name)
with registry.cursor() as cr:
    env = odoo.api.Environment(cr, odoo.SUPERUSER_ID, {})
    pay = env['account.payment'].search([('name', '=', 'PAY00018')])
    if pay:
        print('State:', pay.state)
        print('Move:', pay.move_id.id)
        if pay.move_id:
            for l in pay.move_id.line_ids:
                print('Line:', l.id, 'Account:', l.account_id.id, l.account_id.name, 'Debit:', l.debit, 'Credit:', l.credit)
        print('Valid Liq Accs:', [a.name for a in pay._get_valid_liquidity_accounts()])
        print('Liq Lines:', pay._seek_for_lines()[0])
    else:
        print('Not found')

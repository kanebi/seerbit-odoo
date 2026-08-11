from odoo import api, fields, models, _
from odoo.exceptions import UserError
import logging

from odoo.addons.pos_seerbit.bus_notify import send_seerbit_ui_notification

_logger = logging.getLogger(__name__)


class SeerbitVirtualAccount(models.Model):
    _name = 'seerbit.virtual.account'
    _description = 'Seerbit Virtual Account'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    partner_id = fields.Many2one('res.partner', string='Customer', required=True, ondelete='cascade')
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        required=True,
        default=lambda self: self.env.company,
        index=True,
        help='Branch whose Seerbit keys were used to create this virtual account.',
    )
    reference = fields.Char(string='VA Reference', copy=False, readonly=True)
    account_number = fields.Char(string='Account Number', copy=False, readonly=True)
    bank_name = fields.Char(string='Bank Name', copy=False, readonly=True)
    name = fields.Char(string='VA Name', copy=False, readonly=True)
    payment_ids = fields.One2many('account.payment', 'seerbit_va_id', string='Payments', readonly=True)

    balance = fields.Monetary(
        string='VA Balance',
        compute='_compute_balance',
        currency_field='currency_id',
        help="Outstanding credits (unreconciled inbound payments) associated with this Virtual Account."
    )
    currency_id = fields.Many2one('res.currency', related='company_id.currency_id')

    def _seerbit_company(self):
        """Company that owns this VA's Seerbit business credentials."""
        self.ensure_one()
        return self.env['res.company'].seerbit_company(self.company_id)

    @api.depends('name', 'account_number')
    def _compute_display_name(self):
        for va in self:
            name = va.name or 'Virtual Account'
            account = va.account_number or ''
            va.display_name = f"{name} - {account}" if account else name

    def _compute_balance(self):
        for va in self:
            balance = 0.0
            if va.account_number:
                domain = [
                    ('partner_id', '=', va.partner_id.id),
                    ('seerbit_va_id', '=', va.id),
                    ('payment_type', '=', 'inbound'),
                    ('state', 'in', ['in_process', 'paid']),
                ]
                payments = self.env['account.payment'].search(domain)
                for payment in payments:
                    receivable_lines = payment.move_id.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable')
                    if receivable_lines:
                        balance += sum(abs(l.amount_residual) for l in receivable_lines)
                    else:
                        balance += payment.amount
            va.balance = balance

    @api.model
    def _cron_spool_seerbit_payments(self):
        vas = self.search([('account_number', '!=', False)])
        if not vas:
            return

        from collections import defaultdict
        from ..services.seerbit_api import SeerbitAPI

        vas_by_company = defaultdict(lambda: self.env['seerbit.virtual.account'])
        for va in vas:
            vas_by_company[va.company_id] |= va

        for company, company_vas in vas_by_company.items():
            api_client = SeerbitAPI(self.env, company=company)
            for va in company_vas:
                try:
                    va._fetch_and_process_payments(api_client)
                except Exception as e:
                    _logger.error(
                        "Error spooling payments for VA %s (%s): %s",
                        va.account_number, company.name, e,
                    )
                    send_seerbit_ui_notification(
                        self.env,
                        'Seerbit Sync Error',
                        f'Failed to sync VA {va.name or va.account_number}: {str(e)}',
                        record=va,
                    )

    def _fetch_and_process_payments(self, api_client):
        self.ensure_one()
        seerbit_co = self._seerbit_company()
        try:
            payload = api_client.get_virtual_account_payments(self.account_number)
            for payment in payload:
                if payment.get('gatewayCode') != '00':
                    continue

                ref = payment.get('paymentReference')
                amount = payment.get('amount')

                existing_payment = self.env['account.payment'].search([
                    ('move_id.ref', '=', ref)
                ], limit=1)

                if existing_payment:
                    changed = False
                    if existing_payment.state == 'draft':
                        if seerbit_co.seerbit_auto_post:
                            existing_payment.action_post()
                            changed = True
                    if existing_payment.state == 'in_process':
                        self._reconcile_seerbit_payment(existing_payment)
                        changed = True

                    if changed:
                        send_seerbit_ui_notification(
                            self.env,
                            'Seerbit Payment Updated',
                            f'Virtual Account payment {ref} updated.',
                            record=self,
                        )

                elif amount:
                    self._process_seerbit_va_payment(amount, ref)
                    send_seerbit_ui_notification(
                        self.env,
                        'Seerbit Payment Received',
                        f'VA Payment of {amount} received for {self.partner_id.name}',
                        record=self,
                    )
        except Exception as e:
            _logger.error(f"Error fetching payments: {e}")
            raise UserError(_("Failed to fetch VA payments: %s") % str(e))

    def action_refresh_balance(self):
        self.ensure_one()
        if not self.account_number:
            raise UserError(_("Virtual Account not fully configured."))

        from ..services.seerbit_api import SeerbitAPI
        api_client = SeerbitAPI(self.env, company=self.company_id)

        try:
            self._fetch_and_process_payments(api_client)
        except Exception as e:
            raise UserError(_("Error refreshing balance: %s" % str(e)))

    def action_generate_va(self):
        self.ensure_one()
        if self.account_number:
            raise UserError(_("Virtual Account is already generated."))
        if not self.partner_id.email:
            raise UserError(_("Customer must have an email address to create a Virtual Account."))

        company = self.company_id or self.partner_id.company_id or self.env.company
        if self.company_id != company:
            self.company_id = company

        from ..services.seerbit_api import SeerbitAPI
        api_client = SeerbitAPI(self.env, company=company)
        import uuid
        reference = f"VA_{self.partner_id.id}_{uuid.uuid4().hex[:8]}"
        try:
            res = api_client.create_virtual_account(
                full_name=self.partner_id.name,
                reference=reference,
                email=self.partner_id.email
            )
            payments_data = res.get('payments', {})

            # Create a bank account record in Odoo
            bank = self.env['res.bank'].search([('name', '=', payments_data.get('bankName'))], limit=1)
            if not bank:
                bank = self.env['res.bank'].create({'name': payments_data.get('bankName')})

            self.env['res.partner.bank'].create({
                'acc_number': payments_data.get('accountNumber'),
                'partner_id': self.partner_id.id,
                'bank_id': bank.id,
            })

            self.write({
                'company_id': company.id,
                'reference': payments_data.get('reference', reference),
                'account_number': payments_data.get('accountNumber'),
                'bank_name': payments_data.get('bankName'),
                'name': payments_data.get('walletName'),
            })
        except Exception as e:
            raise UserError(_("Error creating Virtual Account: %s" % str(e)))

    def action_view_payments(self):
        self.ensure_one()
        return {
            'name': _('Virtual Account Payments'),
            'type': 'ir.actions.act_window',
            'res_model': 'account.payment',
            'view_mode': 'list,form',
            'domain': [('seerbit_va_id', '=', self.id)],
            'context': {
                'default_partner_id': self.partner_id.id,
                'default_payment_type': 'inbound',
                'default_seerbit_va_id': self.id,
            }
        }

    def _process_seerbit_va_payment(self, amount, reference):
        """Processes a VA payment: creates payment, reconciles oldest invoices."""
        self.ensure_one()
        partner = self.partner_id
        company = self.company_id

        unpaid_invoice = self.env['account.move'].search([
            ('partner_id', '=', partner.id),
            ('company_id', '=', company.id),
            ('move_type', '=', 'out_invoice'),
            ('state', '=', 'posted'),
            ('payment_state', 'in', ['not_paid', 'partial'])
        ], order='invoice_date asc, id asc', limit=1)

        journal = self.env['account.journal'].search([
            ('type', '=', 'bank'), ('name', 'ilike', 'Seerbit'), ('company_id', '=', company.id)
        ], limit=1)
        if not journal:
            journal = self.env['account.journal'].search([
                ('type', '=', 'bank'), ('company_id', '=', company.id)
            ], limit=1)

        payment_method = self.env.ref('account.account_payment_method_manual_in')

        dest_account_id = False
        if unpaid_invoice:
            invoice_receivable_line = unpaid_invoice.line_ids.filtered(
                lambda l: l.account_id.account_type == 'asset_receivable'
            )
            if invoice_receivable_line:
                dest_account_id = invoice_receivable_line[0].account_id.id

        payment_method_line = (
            journal.inbound_payment_method_line_ids.filtered(
                lambda l: l.payment_method_id == payment_method
            )[:1]
            or journal.inbound_payment_method_line_ids[:1]
        )
        payment_vals = {
            'payment_type': 'inbound',
            'partner_type': 'customer',
            'partner_id': partner.id,
            'amount': float(amount),
            'journal_id': journal.id,
            'company_id': company.id,
            'payment_method_line_id': payment_method_line.id,
            'memo': reference,
            'seerbit_va_id': self.id,
        }
        if dest_account_id:
            payment_vals['destination_account_id'] = dest_account_id

        outstanding_acc = (
            payment_method_line.payment_account_id
            or journal.default_account_id
            or journal.company_id.transfer_account_id
        )
        if outstanding_acc:
            payment_vals['force_outstanding_account_id'] = outstanding_acc.id

        payment = self.env['account.payment'].create(payment_vals)
        seerbit_co = self._seerbit_company()
        auto_post = seerbit_co.seerbit_auto_post
        auto_reconcile = seerbit_co.seerbit_auto_reconcile
        if auto_post:
            payment.action_post()

        if auto_post and auto_reconcile:
            invoices = self.env['account.move'].search([
                ('partner_id', '=', partner.id),
                ('company_id', '=', company.id),
                ('move_type', '=', 'out_invoice'),
                ('state', '=', 'posted'),
                ('payment_state', 'in', ['not_paid', 'partial'])
            ], order='invoice_date asc, id asc')

            payment_lines = payment.move_id.line_ids.filtered(
                lambda line: line.account_id.account_type == 'asset_receivable' and not line.reconciled
            )

            for invoice in invoices:
                if not payment_lines:
                    break
                try:
                    invoice.js_assign_outstanding_line(payment_lines[0].id)
                    invoice.message_post(
                        body=f"Seerbit VA: Auto-reconciled payment of {amount} from VA {self.account_number}"
                    )
                except Exception as e:
                    _logger.error(f"Failed to auto-reconcile VA payment for invoice {invoice.name}: {e}")
                payment_lines = payment.move_id.line_ids.filtered(
                    lambda line: line.account_id.account_type == 'asset_receivable' and not line.reconciled
                )

            self._reconcile_seerbit_payment(payment)

        self.message_post(body=f"Seerbit Payment Received: Amount {amount}, Reference: {reference}")

    def _reconcile_seerbit_payment(self, payment):
        if payment.state != 'in_process':
            return

        liquidity_lines, _, _ = payment._seek_for_lines()
        if not liquidity_lines:
            return

        partner_bank = self.env['res.partner.bank'].search([
            ('partner_id', '=', payment.partner_id.id),
            ('acc_number', '=', self.account_number)
        ], limit=1)

        st_line = self.env['account.bank.statement.line'].create({
            'payment_ref': payment.memo or 'Seerbit Auto Sync',
            'journal_id': payment.journal_id.id,
            'amount': payment.amount if payment.payment_type == 'inbound' else -payment.amount,
            'date': payment.date,
            'partner_id': payment.partner_id.id,
            'partner_bank_id': partner_bank.id if partner_bank else False,
        })

        suspense_line = st_line.move_id.line_ids.filtered(
            lambda l: l.account_id == st_line.journal_id.suspense_account_id
        )
        if suspense_line and liquidity_lines:
            suspense_line.account_id = liquidity_lines.account_id.id
            (suspense_line + liquidity_lines).reconcile()

    def unlink(self):
        from ..services.seerbit_api import SeerbitAPI
        for va in self:
            if not va.reference:
                continue
            api_client = SeerbitAPI(self.env, company=va.company_id)
            try:
                api_client.delete_virtual_account(va.reference)
            except Exception as e:
                _logger.warning(f"Failed to delete VA from Seerbit API: {e}")

            partner_bank = self.env['res.partner.bank'].search([
                ('acc_number', '=', va.account_number),
                ('partner_id', '=', va.partner_id.id)
            ], limit=1)
            if partner_bank:
                partner_bank.active = False
        return super().unlink()

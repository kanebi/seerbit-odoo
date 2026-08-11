# -*- coding: utf-8 -*-
from odoo import models, fields, api
import logging
import uuid

_logger = logging.getLogger(__name__)


class SeerbitPaymentLink(models.Model):
    _name = 'pos_seerbit.payment.link'
    _description = 'Seerbit Payment Link'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc'
    _check_company_auto = True

    move_id = fields.Many2one('account.move', string="Invoice", ondelete='set null', check_company=True)
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    partner_id = fields.Many2one('res.partner', string="Customer")
    email = fields.Char(string="Email")
    currency_id = fields.Many2one(
        'res.currency',
        string="Currency",
        default=lambda self: self.env.company.currency_id,
    )

    name = fields.Char(string="Link Name")
    amount = fields.Float(string="Amount", required=True)
    description = fields.Char(string="Description")
    link_url = fields.Char(string="URL", readonly=True)
    seerbit_link_id = fields.Char(string="Seerbit Link ID", readonly=True)

    state = fields.Selection([
        ('pending', 'Pending'),
        ('paid', 'Paid')
    ], string="Status", default="pending", readonly=True)
    payment_id = fields.Many2one('account.payment', string="Payment", readonly=True)
    payment_ids = fields.One2many('account.payment', 'seerbit_payment_link_id', string="Payments", readonly=True)
    payment_count = fields.Integer(compute='_compute_payment_count')

    def _seerbit_company(self):
        self.ensure_one()
        return self.company_id or (
            self.move_id.company_id if self.move_id else self.env.company
        )

    @api.depends('payment_ids', 'payment_id')
    def _compute_payment_count(self):
        for record in self:
            count = len(record.payment_ids)
            if record.payment_id and record.payment_id not in record.payment_ids:
                count += 1
            record.payment_count = count

    def action_view_payments(self):
        self.ensure_one()
        domain = [('seerbit_payment_link_id', '=', self.id)]
        if self.payment_id:
            domain = ['|', ('seerbit_payment_link_id', '=', self.id), ('id', '=', self.payment_id.id)]
        return {
            'name': 'Payment Link Payments',
            'type': 'ir.actions.act_window',
            'res_model': 'account.payment',
            'view_mode': 'list,form',
            'domain': domain,
            'context': {
                'default_partner_id': self.partner_id.id,
                'default_payment_type': 'inbound',
                'default_seerbit_payment_link_id': self.id,
            }
        }

    @api.onchange('move_id')
    def _onchange_move_id(self):
        if self.move_id:
            self.company_id = self.move_id.company_id
            self.amount = self.move_id.amount_residual
            self.partner_id = self.move_id.partner_id
            self.email = self.move_id.partner_id.email
            self.currency_id = self.move_id.currency_id
            self.description = self.move_id.name

    @api.onchange('partner_id')
    def _onchange_partner_id(self):
        if self.partner_id and not self.email:
            self.email = self.partner_id.email

    def action_open_link(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_url',
            'url': self.link_url,
            'target': 'new',
        }

    def _get_customer_information(self):
        """ Required by mail.template engine when resolving recipients """
        res = {}
        for record in self:
            res[record.id] = {
                'partner_id': record.partner_id,
                'email': record.email or (record.partner_id and record.partner_id.email) or '',
                'name': record.partner_id.name if record.partner_id else '',
            }
        return res

    def action_send_link(self):
        self.ensure_one()
        template = self.env.ref('pos_seerbit.mail_template_seerbit_payment_link', raise_if_not_found=False)
        if not template:
            from odoo.exceptions import UserError
            raise UserError("Mail template for Seerbit Payment Link not found.")

        compose_form = self.env.ref('mail.email_compose_message_wizard_form', raise_if_not_found=False)
        ctx = {
            'default_model': 'pos_seerbit.payment.link',
            'default_res_ids': self.ids,
            'default_template_id': template.id,
            'default_composition_mode': 'comment',
            'force_email': True,
        }
        return {
            'name': 'Send Payment Link',
            'type': 'ir.actions.act_window',
            'view_mode': 'form',
            'res_model': 'mail.compose.message',
            'views': [(compose_form.id if compose_form else False, 'form')],
            'view_id': compose_form.id if compose_form else False,
            'target': 'new',
            'context': ctx,
        }

    def _process_payment(self, amount, reference):
        self.ensure_one()
        if self.state == 'paid':
            return

        partner = self.partner_id or (self.move_id and self.move_id.partner_id)
        company = self._seerbit_company()

        journal = self.env['account.journal'].search([
            ('type', '=', 'bank'), ('name', 'ilike', 'Seerbit'), ('company_id', '=', company.id)
        ], limit=1)
        if not journal:
            journal = self.env['account.journal'].search([
                ('type', '=', 'bank'), ('company_id', '=', company.id)
            ], limit=1)

        payment_method = self.env.ref('account.account_payment_method_manual_in')

        if partner:
            dest_account_id = False
            if self.move_id:
                invoice_receivable_line = self.move_id.line_ids.filtered(
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
                'seerbit_payment_link_id': self.id,
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
            seerbit_co = self.env['res.company'].seerbit_company(company)
            auto_post = seerbit_co.seerbit_auto_post
            auto_reconcile = seerbit_co.seerbit_auto_reconcile
            if auto_post:
                payment.action_post()

            if auto_post and auto_reconcile:
                if self.move_id:
                    payment_lines = payment.move_id.line_ids.filtered(
                        lambda line: line.account_id.account_type == 'asset_receivable' and not line.reconciled
                    )
                    if payment_lines:
                        try:
                            self.move_id.js_assign_outstanding_line(payment_lines[0].id)
                            self.move_id.message_post(
                                body=f"Seerbit Payment Link: Auto-reconciled payment of {amount}"
                            )
                        except Exception as e:
                            _logger.error(
                                "Failed to auto-reconcile payment for invoice %s: %s",
                                self.move_id.name, e,
                            )
                else:
                    payment_lines = payment.move_id.line_ids.filtered(
                        lambda l: l.account_id.account_type == 'asset_receivable' and not l.reconciled
                    )
                    if payment_lines:
                        payment_line = payment_lines[0]
                        unpaid_moves = self.env['account.move'].search([
                            ('partner_id', '=', partner.id),
                            ('company_id', '=', company.id),
                            ('move_type', 'in', ('out_invoice', 'out_refund')),
                            ('state', '=', 'posted'),
                            ('payment_state', 'in', ('not_paid', 'partial'))
                        ], order='invoice_date asc, id asc')

                        for move in unpaid_moves:
                            if payment_line.reconciled:
                                break
                            try:
                                move.js_assign_outstanding_line(payment_line.id)
                                move.message_post(
                                    body=f"Seerbit Payment Link: Auto-reconciled payment of {amount} from standalone link."
                                )
                            except Exception as e:
                                _logger.error(
                                    "Failed to auto-reconcile payment link for invoice %s: %s",
                                    move.name, e,
                                )

                partner.sudo()._reconcile_seerbit_payment(payment)

            self.write({
                'state': 'paid',
                'payment_id': payment.id
            })
            self.message_post(
                body=f"Seerbit Payment Link Paid: Amount {amount}, Reference: {reference}"
            )
        else:
            self.write({'state': 'paid'})

    @api.model_create_multi
    def create(self, vals_list):
        from ..services.seerbit_api import SeerbitAPI

        for vals in vals_list:
            move = False
            if vals.get('move_id'):
                move = self.env['account.move'].browse(vals.get('move_id'))
            company = move.company_id if move else self.env.company
            if not vals.get('company_id'):
                vals['company_id'] = company.id
            else:
                company = self.env['res.company'].browse(vals['company_id'])
            api_client = SeerbitAPI(self.env, company=company)

            if not vals.get('link_url'):
                amount = vals.get('amount') or (move.amount_residual if move else 0.0)

                currency = vals.get('currency_id')
                if not currency:
                    currency_record = move.currency_id if move else company.currency_id
                    currency = currency_record.name
                else:
                    currency = self.env['res.currency'].browse(currency).name

                email = vals.get('email')
                if not email:
                    partner = False
                    if vals.get('partner_id'):
                        partner = self.env['res.partner'].browse(vals.get('partner_id'))
                    elif move:
                        partner = move.partner_id
                    email = partner.email if partner and partner.email else 'no-email@example.com'

                desc = vals.get('description') or (move.name if move else 'Payment Link')

                name = vals.get('name')
                if not name:
                    if move:
                        name = f"INV-{move.id}-{uuid.uuid4().hex[:4]}"
                    else:
                        name = f"LINK-{uuid.uuid4().hex[:4]}"
                    vals['name'] = name

                if not vals.get('amount'):
                    vals['amount'] = amount
                if not vals.get('description'):
                    vals['description'] = desc

                link_data = api_client.create_payment_link(
                    amount=amount,
                    currency=currency,
                    email=email,
                    description=desc,
                    paymentLinkName=name,
                    oneTime=True
                )
                if link_data:
                    vals['link_url'] = link_data.get('paymentLinkUrl')
                    vals['seerbit_link_id'] = link_data.get('paymentLinkId')

        return super().create(vals_list)

    def write(self, vals):
        res = super().write(vals)
        if any(f in vals for f in ['name', 'amount', 'description', 'email', 'currency_id']):
            from ..services.seerbit_api import SeerbitAPI
            for record in self.filtered(lambda r: r.seerbit_link_id):
                company = record._seerbit_company()
                api_client = SeerbitAPI(self.env, company=company)
                currency_name = record.currency_id.name if record.currency_id else company.currency_id.name

                email = record.email
                if not email:
                    partner = record.partner_id or (record.move_id and record.move_id.partner_id)
                    email = partner.email if partner else 'no-email@example.com'

                api_client.update_payment_link(
                    payment_link_id=record.seerbit_link_id,
                    amount=record.amount,
                    currency=currency_name,
                    email=email,
                    description=record.description or (record.move_id.name if record.move_id else 'Payment Link'),
                    paymentLinkName=record.name or (record.move_id.name if record.move_id else 'Payment Link'),
                    oneTime=True
                )
        return res

    def unlink(self):
        from ..services.seerbit_api import SeerbitAPI
        for record in self:
            if record.seerbit_link_id:
                company = record._seerbit_company()
                api_client = SeerbitAPI(self.env, company=company)
                api_client.delete_payment_link(record.seerbit_link_id)
        return super().unlink()

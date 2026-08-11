# -*- coding: utf-8 -*-
from odoo import models, fields, api

class PaymentLinkWizard(models.TransientModel):
    _inherit = 'payment.link.wizard'

    generate_seerbit_link = fields.Boolean(string="Generate and send with Seerbit")
    seerbit_link_name = fields.Char(string="Seerbit Link Name")
    seerbit_link_url = fields.Char(string="Seerbit Link", readonly=True)
    is_invoice_move = fields.Boolean(compute='_compute_is_invoice_move')

    @api.depends('res_model', 'res_id')
    def _compute_is_invoice_move(self):
        for wizard in self:
            if wizard.res_model == 'account.move' and wizard.res_id:
                move = self.env['account.move'].browse(wizard.res_id)
                wizard.is_invoice_move = move.move_type == 'out_invoice'
            else:
                wizard.is_invoice_move = False

    def action_generate_seerbit_link(self):
        self.ensure_one()
        if self.res_model != 'account.move':
            return
            
        move = self.env['account.move'].browse(self.res_id)
        
        from ..services.seerbit_api import SeerbitAPI
        api_client = SeerbitAPI(self.env)
        
        import uuid
        sanitized_name = f"INV-{move.id}-{uuid.uuid4().hex[:4]}"
        
        link_name = self.seerbit_link_name or sanitized_name
        
        link_data = api_client.create_payment_link(
            amount=self.amount,
            currency=self.currency_id.name,
            email=self.partner_email or 'no-email@example.com',
            description=move.name,
            paymentLinkName=link_name,
            oneTime=True
        )
        
        if link_data and link_data.get('paymentLinkUrl'):
            self.seerbit_link_url = link_data['paymentLinkUrl']
            
            self.env['pos_seerbit.payment.link'].create({
                'move_id': move.id,
                'name': link_name,
                'amount': self.amount,
                'description': move.name,
                'link_url': link_data['paymentLinkUrl'],
                'seerbit_link_id': link_data.get('paymentLinkId')
            })
            
        return {
            'name': 'Generate Payment Link',
            'type': 'ir.actions.act_window',
            'res_model': 'payment.link.wizard',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

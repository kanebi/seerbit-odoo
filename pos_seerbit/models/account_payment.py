# -*- coding: utf-8 -*-
from odoo import models, fields

class AccountPayment(models.Model):
    _inherit = 'account.payment'

    seerbit_va_id = fields.Many2one(
        'seerbit.virtual.account', 
        string='Seerbit Virtual Account',
        help="The Virtual Account that received or initiated this payment."
    )
    seerbit_payment_link_id = fields.Many2one(
        'pos_seerbit.payment.link',
        string='Seerbit Payment Link',
        help="The Payment Link that received this payment."
    )

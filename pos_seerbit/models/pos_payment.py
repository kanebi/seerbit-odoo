# -*- coding: utf-8 -*-
from odoo import api, fields, models


class PosPayment(models.Model):
    _inherit = 'pos.payment'

    seerbit_va_transaction_id = fields.Many2one(
        'seerbit.va.transaction',
        string="SeerBit Transaction",
        copy=False,
        readonly=True
    )
    seerbit_transaction_ref = fields.Char(
        string="SeerBit Reference",
        copy=False,
        readonly=True
    )
    seerbit_payer_name = fields.Char(
        string="SeerBit Payer Name",
        copy=False,
        readonly=True
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            ref = vals.get('seerbit_transaction_ref') or vals.get('transaction_id')
            if ref:
                if not vals.get('transaction_id'):
                    vals['transaction_id'] = ref
                if not vals.get('payment_ref_no'):
                    vals['payment_ref_no'] = ref
                if not vals.get('seerbit_transaction_ref'):
                    vals['seerbit_transaction_ref'] = ref
            payer = vals.get('seerbit_payer_name') or vals.get('cardholder_name')
            if payer:
                if not vals.get('cardholder_name'):
                    vals['cardholder_name'] = payer
                if not vals.get('seerbit_payer_name'):
                    vals['seerbit_payer_name'] = payer
        return super().create(vals_list)



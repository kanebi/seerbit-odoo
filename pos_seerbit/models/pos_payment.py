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


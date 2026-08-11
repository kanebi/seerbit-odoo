# -*- coding: utf-8 -*-
from odoo import models, fields, api
import threading
import odoo

def threaded_send_invoice(db_name, uid, invoice_no):
    registry = odoo.registry(db_name)
    with registry.cursor() as cr:
        env = api.Environment(cr, uid, {})
        from ..services.seerbit_api import SeerbitAPI
        api_client = SeerbitAPI(env)
        try:
            api_client.send_invoice(invoice_no)
        except Exception:
            pass

class AccountMoveSendWizard(models.TransientModel):
    _inherit = 'account.move.send.wizard'

    send_with_seerbit = fields.Boolean(
        string="Send via Seerbit",
        default=lambda self: self.env['ir.config_parameter'].sudo().get_param('pos_seerbit.default_send_with_seerbit', 'False') == 'True'
    )

    def action_send_and_print(self, allow_fallback_pdf=False):
        # Save the preference if it changed
        current_default = self.env['ir.config_parameter'].sudo().get_param('pos_seerbit.default_send_with_seerbit', 'False') == 'True'
        if self.send_with_seerbit != current_default:
            self.env['ir.config_parameter'].sudo().set_param('pos_seerbit.default_send_with_seerbit', str(self.send_with_seerbit))

        # Call the original send and print logic
        res = super().action_send_and_print(allow_fallback_pdf=allow_fallback_pdf)

        for wizard in self:
            if wizard.send_with_seerbit and wizard.move_id:
                # Sync if not already synced
                if not wizard.move_id.synced_with_seerbit:
                    wizard.move_id.action_sync_seerbit_invoice()
                
                # If successfully synced, it will have a seerbit_invoice_no
                if wizard.move_id.seerbit_invoice_no:
                    # Run send_invoice asynchronously so the UI modal closes early
                    threading.Thread(
                        target=threaded_send_invoice, 
                        args=(self.env.cr.dbname, self.env.uid, wizard.move_id.seerbit_invoice_no)
                    ).start()

        return res

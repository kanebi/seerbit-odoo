from odoo import api, fields, models, _

class SeerbitPayoutOtpWizard(models.TransientModel):
    _name = 'seerbit.payout.otp.wizard'
    _description = 'Enter OTP for Seerbit Payout'

    payout_id = fields.Many2one('seerbit.payout', string='Payout', required=True)
    otp = fields.Char(string='OTP', required=True, help="Enter the OTP received from Seerbit")

    def action_confirm(self):
        self.ensure_one()
        if self.payout_id and self.otp:
            self.payout_id.action_submit_payout(self.otp)

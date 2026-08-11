import logging
import json
from odoo import http
from odoo.http import request

from odoo.addons.pos_seerbit.bus_notify import send_seerbit_ui_notification

_logger = logging.getLogger(__name__)

class SeerbitWebhookController(http.Controller):

    @http.route('/api/seerbit/webhook', type='http', auth='public', methods=['POST'], csrf=False)
    def handle_webhook(self, **kwargs):
        """
        Endpoint to receive webhook notifications from Seerbit.
        Handles three payment types:
          1. Payment Link payments  (matched by paymentLinkId)
          2. Invoice payments       (matched by invoiceNumber)
          3. Virtual Account payments (matched by accountNumber)
        """
        try:
            payload = json.loads(request.httprequest.data)
        except Exception as e:
            _logger.warning(f"Seerbit Webhook failed to parse JSON: {e}")
            return request.make_response(json.dumps({"status": "error", "message": "Invalid JSON"}), headers={'Content-Type': 'application/json'})

        if not payload:
            _logger.warning("Seerbit Webhook received empty payload.")
            return request.make_response(json.dumps({"status": "error", "message": "Empty payload"}), headers={'Content-Type': 'application/json'})

        _logger.info(f"Seerbit Webhook received: {json.dumps(payload)}")
        
        # Parse standard Seerbit structure
        notification_items = payload.get('notificationItems', [])
        if not notification_items:
            # Fallback if it's sent differently
            payment_data = payload.get('data', payload)
            items = [payment_data] if payment_data else []
        else:
            items = [item.get('notificationRequestItem', {}).get('data', {}) 
                     for item in notification_items 
                     if item.get('notificationRequestItem', {}).get('eventType') in ('transaction', 'transaction.wallet')]
            
        for payment_data in items:
            # -----------------------------------------------------------
            # Extract common fields from the webhook payload
            # -----------------------------------------------------------
            # Virtual Account identifier (VA payments)
            account_number = payment_data.get('creditAccountNumber') or payment_data.get('accountNumber')
            # Payment Link identifier (link-based payments)
            payment_link_id = payment_data.get('paymentLinkId')
            # Seerbit Invoice number (invoice-based payments)
            invoice_number = payment_data.get('invoiceNumber')
            
            # Amount: try top-level first, fall back to paymentBreakdown
            amount = payment_data.get('amount')
            if not amount:
                payment_breakdown = payment_data.get('paymentBreakdown', {})
                amount = payment_breakdown.get('total') or payment_breakdown.get('amount')
                
            # Transaction reference for duplicate detection
            reference = payment_data.get('reference') or payment_data.get('paymentReference')
            # Gateway status code ('00' = success)
            gateway_code = payment_data.get('gatewayCode')
            # Merchant public key for verification
            public_key = payment_data.get('publicKey')
            
            # Skip if amount is missing — can't process without it
            if not amount:
                _logger.warning(f"Seerbit Webhook missing amount: {payment_data}")
                continue
            
            # Skip non-successful transactions (only '00' means approved)
            if str(gateway_code) != '00':
                _logger.info(f"Seerbit Webhook ignoring non-success gatewayCode: {gateway_code}")
                continue

            Company = request.env['res.company'].sudo()
            # Prefer business-object company; public key is verification / last resort
            # (legacy 0.2.0 migration may have duplicated the same global key on many companies).
            key_company = Company.seerbit_company_by_public_key(public_key) if public_key else Company.browse()

            def _company_key_ok(company):
                """Reject if webhook publicKey is set and does not match this company's key."""
                if not company or not public_key:
                    return bool(company)
                expected = (company.seerbit_public_key or '').strip()
                if expected and expected != public_key.strip():
                    _logger.warning(
                        "Seerbit Webhook: publicKey mismatch for %s (payload key != company key)",
                        company.name,
                    )
                    return False
                return True

            # ===========================================================
            # SECTION 1: Payment Link Payments
            # Matched by paymentLinkId → pos_seerbit.payment.link.seerbit_link_id
            # ===========================================================
            if payment_link_id:
                link = request.env['pos_seerbit.payment.link'].sudo().search(
                    [('seerbit_link_id', '=', str(payment_link_id))], limit=1
                )
                if link:
                    company = link.company_id or (
                        link.move_id.company_id
                        if link.move_id
                        else (link.partner_id.company_id or key_company)
                    )
                    if not _company_key_ok(company):
                        _logger.warning(
                            "Seerbit Webhook: cannot resolve company for payment link %s",
                            payment_link_id,
                        )
                        continue
                    # Check if this reference was already processed (duplicate webhook)
                    if reference:
                        existing_payment = request.env['account.payment'].sudo().search(
                            [('move_id.ref', '=', reference)], limit=1
                        )
                        if existing_payment:
                            if existing_payment.state == 'draft':
                                seerbit_co = Company.seerbit_company(company)
                                if seerbit_co.seerbit_auto_post:
                                    _logger.info(
                                        "Seerbit Webhook: Found existing pending payment %s. Posting it...",
                                        reference,
                                    )
                                    existing_payment.sudo().action_post()
                            if existing_payment.state == 'in_process':
                                link.move_id.partner_id.sudo()._reconcile_seerbit_payment(existing_payment)
                            _logger.info(
                                "Seerbit Webhook: Payment Link %s already processed.", payment_link_id
                            )
                            continue
                    try:
                        link._process_payment(amount, reference or f"Link Payment {payment_link_id}")
                        send_seerbit_ui_notification(
                            request.env,
                            'Seerbit Payment Received',
                            f'Payment received for Link: {link.name}',
                            record=link,
                        )
                        _logger.info("Seerbit Webhook: Processed payment link for %s", link.name)
                    except Exception as e:
                        _logger.error("Seerbit Webhook Error processing payment link: %s", e)
                        send_seerbit_ui_notification(
                            request.env,
                            'Seerbit Webhook Error',
                            f'Failed to process Link payment: {str(e)}',
                            record=link,
                        )
                else:
                    _logger.warning(
                        "Seerbit Webhook: Payment Link ID %s not found in DB.", payment_link_id
                    )
                continue

            # ===========================================================
            # SECTION 2: Seerbit Invoice Payments
            # Matched by invoiceNumber → account.move.seerbit_invoice_no
            # ===========================================================
            elif invoice_number:
                move = request.env['account.move'].sudo().search([
                    ('seerbit_invoice_no', '=', invoice_number),
                    ('move_type', '=', 'out_invoice'),
                ], limit=1)
                if not move:
                    _logger.warning(
                        "Seerbit Webhook: Invoice number %s not found in Odoo.", invoice_number
                    )
                    continue

                company = move.company_id
                if not _company_key_ok(company):
                    continue

                if move.payment_state in ('paid', 'in_payment', 'reversed'):
                    _logger.info(
                        "Seerbit Webhook: Invoice %s (%s) already settled. Skipping.",
                        move.name, invoice_number,
                    )
                    continue

                if reference:
                    existing_payment = request.env['account.payment'].sudo().search(
                        [('move_id.ref', '=', reference)], limit=1
                    )
                    if existing_payment:
                        if existing_payment.state == 'draft':
                            seerbit_co = Company.seerbit_company(move.company_id)
                            if seerbit_co.seerbit_auto_post:
                                existing_payment.sudo().action_post()
                        if existing_payment.state == 'in_process':
                            move.partner_id.sudo()._reconcile_seerbit_payment(existing_payment)
                        _logger.info(
                            "Seerbit Webhook: Invoice payment %s already processed.", reference
                        )
                        continue

                try:
                    journal = request.env['account.journal'].sudo().search(
                        [('type', '=', 'bank'), ('name', 'ilike', 'Seerbit'),
                         ('company_id', '=', move.company_id.id)], limit=1
                    )
                    if not journal:
                        journal = request.env['account.journal'].sudo().search(
                            [('type', '=', 'bank'), ('company_id', '=', move.company_id.id)], limit=1
                        )

                    payment_method = request.env.ref('account.account_payment_method_manual_in')

                    invoice_receivable_line = move.line_ids.filtered(
                        lambda l: l.account_id.account_type == 'asset_receivable'
                    )
                    dest_account_id = (
                        invoice_receivable_line[0].account_id.id if invoice_receivable_line else False
                    )

                    payment_method_line = journal.inbound_payment_method_line_ids.filtered(
                        lambda l: l.payment_method_id == payment_method
                    )[:1] or journal.inbound_payment_method_line_ids[:1]

                    payment_vals = {
                        'payment_type': 'inbound',
                        'partner_type': 'customer',
                        'partner_id': move.partner_id.id,
                        'amount': float(amount),
                        'journal_id': journal.id,
                        'company_id': move.company_id.id,
                        'payment_method_line_id': payment_method_line.id,
                        'memo': reference or f"Invoice {invoice_number}",
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

                    payment = request.env['account.payment'].sudo().create(payment_vals)
                    seerbit_co = Company.seerbit_company(move.company_id)
                    auto_post = seerbit_co.seerbit_auto_post
                    auto_reconcile = seerbit_co.seerbit_auto_reconcile
                    if auto_post:
                        payment.action_post()

                    if auto_post and auto_reconcile:
                        payment_lines = payment.move_id.line_ids.filtered(
                            lambda line: line.account_id.account_type == 'asset_receivable'
                            and not line.reconciled
                        )
                        if payment_lines:
                            try:
                                move.js_assign_outstanding_line(payment_lines[0].id)
                            except Exception as e:
                                _logger.error(
                                    "Failed to auto-reconcile payment for invoice %s: %s",
                                    move.name, e,
                                )

                        if payment.state == 'in_process':
                            move.partner_id.sudo()._reconcile_seerbit_payment(payment)

                    move.sudo().write({'seerbit_invoice_status': 'PAID'})
                    move.message_post(
                        body=f"Seerbit Webhook: Invoice marked as PAID. Amount: {amount}, Ref: {invoice_number}"
                    )

                    send_seerbit_ui_notification(
                        request.env,
                        'Seerbit Invoice Paid',
                        f'Invoice {move.name} paid via Seerbit ({invoice_number})',
                        record=move,
                    )
                    _logger.info(
                        "Seerbit Webhook: Processed invoice payment for %s (%s)",
                        move.name, invoice_number,
                    )
                except Exception as e:
                    _logger.error(
                        "Seerbit Webhook Error processing invoice payment for %s: %s",
                        invoice_number, e, exc_info=True,
                    )
                    send_seerbit_ui_notification(
                        request.env,
                        'Seerbit Webhook Error',
                        f'Failed to process Invoice {invoice_number}: {str(e)}',
                        record=move,
                    )
                continue

            # ===========================================================
            # SECTION 3: Virtual Account (VA) Payments
            # Matched by creditAccountNumber/accountNumber → seerbit.virtual.account
            # ===========================================================
            elif account_number:
                va = request.env['seerbit.virtual.account'].sudo().search(
                    [('account_number', '=', account_number)], limit=1
                )
                if not va:
                    _logger.warning(
                        "Seerbit Webhook: No Virtual Account found for %s", account_number
                    )
                    continue

                company = va.company_id or key_company
                if not _company_key_ok(company):
                    _logger.warning(
                        "Seerbit Webhook: cannot resolve company for VA %s", account_number
                    )
                    continue

                if reference:
                    existing_payment = request.env['account.payment'].sudo().search(
                        [('move_id.ref', '=', reference)], limit=1
                    )
                    if existing_payment:
                        if existing_payment.state == 'draft':
                            seerbit_co = Company.seerbit_company(company)
                            if seerbit_co.seerbit_auto_post:
                                _logger.info(
                                    "Seerbit Webhook: Found existing pending payment %s. Posting it...",
                                    reference,
                                )
                                existing_payment.sudo().action_post()
                        if existing_payment.state == 'in_process':
                            va.sudo()._reconcile_seerbit_payment(existing_payment)

                        _logger.info(
                            "Seerbit Webhook: Payment %s already processed/posted.", reference
                        )
                        continue

                try:
                    va.sudo()._process_seerbit_va_payment(amount, reference or "Webhook Payment")
                    send_seerbit_ui_notification(
                        request.env,
                        'Seerbit Payment Received',
                        f'VA Payment of {amount} received for {va.partner_id.name}',
                        record=va,
                    )
                    _logger.info(
                        "Seerbit Webhook: Processed payment of %s for %s",
                        amount, va.partner_id.name,
                    )
                except Exception as e:
                    _logger.error("Seerbit Webhook Error processing payment: %s", e)
                    send_seerbit_ui_notification(
                        request.env,
                        'Seerbit Webhook Error',
                        f'Failed to process VA payment: {str(e)}',
                        record=va,
                    )

            else:
                _logger.warning(
                    "Seerbit Webhook: Unrecognized payment type — no paymentLinkId, "
                    "invoiceNumber, or accountNumber. Payload: %s",
                    payment_data,
                )

        return request.make_response(json.dumps({"status": "success", "message": "Processed"}), headers={'Content-Type': 'application/json'})

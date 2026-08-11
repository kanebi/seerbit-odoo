# Seerbit Payment & Reconciliation Guide

This document explains the accounting workflows handling all inbound and outbound payments within the `pos_seerbit` Odoo integration. It details how payments are generated, how they reconcile against invoices/bills, what can go wrong, and how to troubleshoot.

---

## 1. Accounting Workflows & Entries

The integration bridges Seerbit's payment ecosystem with Odoo's strict double-entry accounting.

### A. Invoice Status Sync (Cron & Manual Check)
**Trigger**: A scheduled cron job polls Seerbit for the status of posted, un-paid invoices, or a user manually clicks "Check Status" on an invoice.
**Entry Creation**: 
- If Seerbit reports `PAID`, Odoo creates an **Inbound Customer Payment** (`account.payment`).
- The payment amount uses the `totalAmount` returned by the Seerbit API.
- The payment's `destination_account_id` is dynamically configured to perfectly match the target invoice's Accounts Receivable (AR) account.

### B. Real-time Webhooks
**Trigger**: Seerbit pushes an event to the Odoo webhook controller when a payment succeeds.
**Entry Creation**:
- Similar to the cron job, an **Inbound Customer Payment** is instantly generated.
- Designed to handle duplicates safely by searching existing payments matching the Seerbit `reference`.

### C. Standalone Payment Links
**Trigger**: A customer pays via an independent payment link.
**Entry Creation**:
- Can be tied to a specific invoice, or be a "blind deposit" tied only to the customer profile.
- If tied to an invoice, it adopts that invoice's AR account. 
- If it is a blind deposit, Odoo attempts a **FIFO (First-In-First-Out)** reconciliation to clear the oldest unpaid invoices for that customer sequentially.

### D. Virtual Accounts
**Trigger**: A customer wires money directly into their dedicated Seerbit Virtual Account.
**Entry Creation**:
- Odoo treats this as an inbound blind deposit. 
- **Predictive Matching**: To prevent AR mismatches, the script "peeks" at the customer's oldest unpaid invoice and adopts its AR account *before* creating the payment.
- The payment is then pushed through a FIFO reconciliation loop, paying off as many invoices as the transferred amount covers.

### E. Vendor Payouts (Outbound)
**Trigger**: The user processes a Vendor Payout against an Odoo Bill.
**Entry Creation**:
- Creates an **Outbound Supplier Payment**.
- Adopts the Accounts Payable (AP) account of the target bill.

---

## 2. Reconciliation Expectations

To effectively transition an invoice from `Posted` to `In Payment` or `Paid`, the payment must be reconciled with the invoice. The integration uses Odoo's native `js_assign_outstanding_line()` engine to handle this.

**Strict AR/AP Matching Rule:** 
Odoo 16+ enforces that the AR/AP account on the payment **must perfectly match** the AR/AP account on the invoice. If an invoice was generated from a Point of Sale session, it might use a `POS Receivable Account` instead of the customer's generic `Trade Receivable Account`. 

Our integration automatically overrides the payment's destination account to ensure it mimics the invoice, guaranteeing smooth reconciliation regardless of the source of the invoice.

---

## 3. Possible Failures & Troubleshooting

### Failure 1: "Invoice status is PAID on Seerbit, but the invoice in Odoo is Unpaid."
**Cause**: The payment was created but failed to link (reconcile) with the invoice. This was historically caused by AR account mismatches (e.g. POS AR vs Generic AR).
**Fix**: 
1. The codebase is now patched to dynamically assign `destination_account_id` so this should not occur for new payments. 
2. **Manual Fix**: If you find old invoices in this state, go to the Invoice view, scroll to the bottom, and click the **Add** button under "Outstanding Payments" to manually link the orphaned payment.

### Failure 2: "Multiple In-Process Payments created for the same Invoice."
**Cause**: If the reconciliation step fails silently, the invoice's `payment_state` remains `not_paid`. The cron job runs again, sees the invoice is still unpaid, and creates another payment.
**Fix**:
1. With the AR matching patch deployed, the first payment will reconcile successfully, preventing duplicate triggers.
2. **Manual Fix**: Go to Accounting > Customers > Payments, identify the duplicate "In Process" payments for that invoice reference, and manually **Cancel** or delete the duplicates. Keep only the one that is reconciled.

### Failure 3: "Virtual Account deposit wasn't applied to the correct invoice."
**Cause**: Virtual Account transfers are blind deposits processed using FIFO. If the customer has multiple older unpaid invoices, the deposit will pay those off first before applying to the newest invoice.
**Fix**: 
1. This is expected accounting behavior. 
2. If you need to reallocate the funds, go to the older invoice, click the **"i" icon** on the payment line, and click **Unreconcile**. Then, go to the target invoice and assign the newly freed outstanding payment.

### Failure 4: "Seerbit Webhook failed to update Odoo."
**Cause**: Network timeouts or temporary Odoo server downtime.
**Fix**: 
1. The system is designed to be resilient. If the webhook fails, the fallback cron job (`action_check_all_seerbit_status`) will sweep through and catch any unsynced or unpaid invoices on its next scheduled run.

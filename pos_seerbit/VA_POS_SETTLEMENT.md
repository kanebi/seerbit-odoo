# Virtual Account to Point of Sale Settlement

**Module:** `pos_seerbit`
**Status:** Specification, pending implementation
**Scope:** Routing inbound Seerbit Virtual Account credits to Point of Sale orders when the Virtual Account belongs to a till cashier.

For the general accounting model covering invoices, payment links, virtual account credits and vendor payouts, see `SEERBIT_ACCOUNTING_GUIDE.md`. This document is limited to the cashier case and to the settlement of Seerbit payment lines on `pos.order`.

---

## 1. Background

The integration currently runs two independent settlement pipelines.

| Pipeline | Trigger | Behaviour |
| --- | --- | --- |
| Virtual Account | `POST /api/seerbit/webhook`, or the cron `seerbit.virtual.account._cron_spool_seerbit_payments` | Creates an inbound `account.payment` against `va.partner_id`, reconciles that partner's unpaid invoices in FIFO order, then performs bank reconciliation. |
| Point of Sale | Cashier adds a Seerbit payment line on the Payment Screen | The browser writes the request to Firestore keyed on `order.uuid`, subscribes to the `reconciliations` collection, and on success sets the line to `done` with a transaction reference before the order is validated. |

Cashiers hold Virtual Accounts in the same way customers do, because a Virtual Account is provisioned against `res.partner`. When a customer transfers into a cashier's Virtual Account at the till, the Virtual Account pipeline interprets the credit as a payment made *by the cashier*, and applies it to the cashier's own receivables. The sale that the money was intended for remains unpaid.

A second constraint applies. Point of Sale settlement is presently client side only. The Odoo 19 JavaScript reacts to Firestore and `localStorage`; it no longer polls `pos.payment.method.seerbit_latest_response`. A Virtual Account credit therefore has no existing route into a Seerbit payment line.

---

## 2. Objectives

1. Detect that the owner of a credited Virtual Account is working a till, on the evidence of an open session or an attributed order rather than on configuration permissions.
2. Settle the credit against that cashier's Seerbit payment line rather than against the cashier's receivables.
3. Prefer orders in an open session; fall back to the cashier's other Seerbit orders at the same amount.
4. Apply the transaction reference using the same fields the Point of Sale writes during a live terminal payment.
5. Preserve existing Virtual Account behaviour for genuine customer accounts.
6. Guarantee that a single Seerbit reference settles exactly once.

---

## 3. Existing implementation points

| Concern | Location |
| --- | --- |
| Virtual Account webhook | `controllers/webhook.py`, Section 3 (`accountNumber` / `creditAccountNumber`) |
| Virtual Account polling | `models/virtual_account.py`, `_fetch_and_process_payments` |
| Credit booking and FIFO reconciliation | `models/virtual_account.py`, `_process_seerbit_va_payment`, `_reconcile_seerbit_payment` |
| Terminal request and success handling | `static/src/js/payment_seerbit.js` |
| Firestore subscription | `static/src/js/firebase_listener.js`, matching `reconciliations.id == order.uuid` |
| Payment line states in the UI | `static/src/js/PaymentScreen.js`, `static/src/xml/PaymentScreenPaymentLines.xml` |
| Backend notification bus | `bus_notify.py`, `static/src/js/bus_listener.js` |
| Cashier to till association | `pos_hr`: `pos.config.basic_employee_ids`, `advanced_employee_ids`, `minimal_employee_ids`, and `pos.order.employee_id`. Without `pos_hr`, `pos.session.user_id`. |
| Virtual Account ownership | `seerbit.virtual.account.partner_id` |

---

## 4. Settlement flow

The matcher is inserted ahead of `_process_seerbit_va_payment`. It does not replace the existing Virtual Account logic; it decides whether that logic should run at all.

```
Virtual Account credit (webhook or cron)
        |
        v
Idempotency check on the Seerbit reference
        |
        v
Resolve owner: va.partner_id -> res.users -> hr.employee
        |
        +-- no employee behind the partner ----------> Virtual Account credit path
        |
        v
Stage 1: open sessions
  Open sessions of the company on which the owner
  is a cashier, then draft orders attributed to that
  owner holding an unsettled Seerbit line of the
  exact amount
        |
        +-- match found ----------------------------> settle line, notify till
        |
        v
Stage 2: cashier profile
  Any order attributed to the owner, in any session,
  holding a Seerbit line of the exact amount with no
  transaction reference recorded
        |
        +-- match found ----------------------------> stamp transaction reference
        |
        v
No match -----------------------------------------> Virtual Account credit path
```

### 4.1 Owner resolution

The Virtual Account partner is resolved to an employee and a user through the standard links:

```
va.partner_id
    -> res.users where partner_id = va.partner_id
    -> hr.employee where user_id = that user, or work_contact_id = va.partner_id
```

Where no employee or user stands behind the partner, the credit is a customer payment and control passes directly to the Virtual Account credit path.

Resolution alone does not establish that the owner is a cashier. That determination is made per stage, against evidence of actual till activity rather than against configuration permissions. Employee lists on `pos.config` are frequently left empty, which Odoo interprets as permitting every employee; treating an empty list as qualifying would admit any employee holding a Virtual Account. Configuration permissions are therefore used only to narrow a session set that has already been established, never to qualify the owner on their own.

### 4.2 Stage 1: open sessions

Stage 1 is the primary route. It targets the situation the feature exists for: a customer is paying at the till right now, and a Seerbit line is waiting.

The search is session driven and proceeds in two steps.

**Step 1: establish the session set.** Select `pos.session` records where:

| Field | Condition |
| --- | --- |
| `state` | `opened` |
| `config_id.company_id` | Equal to `va.company_id` |
| Cashier association | The resolved employee appears in `config_id.basic_employee_ids`, `advanced_employee_ids` or `minimal_employee_ids`; or `user_id` is the resolved user |

Where `pos_hr` is not installed, the employee lists do not exist and the association reduces to `user_id`. Where the lists exist but are empty, the configuration contributes no session on that basis; the owner must reach the session through `user_id`, or through an order attributed to them in step 2.

If the session set is empty, Stage 1 returns no candidate.

**Step 2: find the line.** Within those sessions, select `pos.order` records where:

| Field | Condition |
| --- | --- |
| `session_id` | In the session set from step 1 |
| Attribution | `employee_id` equal to the resolved employee; or, where `pos_hr` is absent, `session_id.user_id` equal to the resolved user |
| `state` | `draft` |
| `payment_ids.payment_method_id.use_payment_terminal` | `seerbit` |
| `payment_ids.payment_status` | One of `waiting`, `waitingSeerbit`, `pending`, `waitingCard`, or unset |
| `payment_ids.transaction_id` | Not set |
| `payment_ids.amount` | Exactly equal to the credit amount, compared with `currency_id.compare_amounts` |

The attribution condition is what prevents a credit from settling another cashier's line when two people share a till. Odoo does not persist a currently logged-in cashier on `pos.session`, so `pos.order.employee_id` is the closest reliable signal of who is ringing the sale.

Where more than one candidate line remains, apply the ordering rule in section 4.4. A Stage 1 match is settled as described in section 4.5 and the till is notified so the cashier can complete the order.

### 4.3 Stage 2: cashier profile

Stage 2 runs only when Stage 1 returns nothing. It covers the case where the order was already validated, whether because the cashier forced it through, because the session has since been closed, or because the credit arrived late.

Stage 2 needs no separate cashier test. Its attribution condition is the test: an order carrying the owner's `employee_id` and a Seerbit payment line is itself the evidence that the owner works a till.

Search `pos.order` under the following conditions.

| Field | Condition |
| --- | --- |
| `company_id` | Equal to `va.company_id` |
| Attribution | `employee_id` equal to the resolved employee, or `session_id.user_id` equal to the resolved user where `pos_hr` is absent |
| `state` | Any of `draft`, `paid`, `done` |
| `payment_ids.payment_method_id.use_payment_terminal` | `seerbit` |
| `payment_ids.amount` | Exactly equal to the credit amount |
| `payment_ids.transaction_id` | Not set |
| `payment_ids.payment_ref_no` | Not set |

Stage 2 does not create accounting entries and does not revalidate the order. It records the Seerbit transaction reference on the payment line, exactly as the Point of Sale does on a successful terminal payment, so the sale carries provider traceability.

An amount comparison is the only available key at this stage, because the webhook payload does not reliably carry the order UUID that Firestore uses. Section 8 records the consequences and the mitigation.

### 4.4 Candidate ordering

Where a stage returns more than one candidate line, selection is deterministic rather than arbitrary:

1. Lines on orders in an open session, ahead of lines on orders in a closed session.
2. The oldest unsettled line by `pos.payment.payment_date`, then by `id`.

The selected line is settled. A warning is written to the Virtual Account chatter naming the number of candidates and the line chosen, so the choice can be reviewed.

### 4.5 Settlement of a payment line

Settlement reproduces, on the server, the outcome the browser produces on a successful terminal payment. The field mapping mirrors `payment_seerbit.js`.

| `pos.payment` field | Value |
| --- | --- |
| `payment_status` | `done` |
| `transaction_id` | Seerbit `paymentReference`, or the session identifier where supplied |
| `payment_ref_no` | The same reference |
| `ticket` | `Transaction ID: <reference>` |
| `card_type` | `Seerbit` |
| `cardholder_name` | `Seerbit Payment` |

Following the write:

1. A bus notification of type `seerbit_pos_va_settled` is published, carrying `pos_order_id`, `order_uuid`, `pos_payment_uuid`, the amount and the reference.
2. Where the order is open on a till, the Point of Sale client marks the line `done` and applies the same completion path as a successful terminal payment, validating the order when it is fully paid.
3. A message is posted to the Virtual Account chatter recording the amount, the Seerbit reference and the linked order.
4. `_process_seerbit_va_payment` is **not** invoked for this credit.

Point of Sale takings are posted to the Seerbit journal when the session is closed. Creating an `account.payment` against the cashier in addition to the settled line would record the same cash twice.

### 4.6 Idempotency

Before any write, the credit is discarded when any of the following already exists:

- An `account.payment` whose `move_id.ref` or `memo` equals the Seerbit reference. This is the check already performed by the webhook and the spooler.
- A `pos.payment` whose `transaction_id` or `payment_ref_no` equals the Seerbit reference.

One Seerbit reference produces one settlement, either on the Point of Sale or on the Virtual Account, and never on both.

### 4.7 Fallback

Where no employee or user stands behind the Virtual Account partner, or neither stage produces a candidate, the credit is passed to the existing `_process_seerbit_va_payment`. A notification records that the credit was booked as a Virtual Account payment rather than matched to a till.

---

## 5. Delivery plan

### Phase 1: matcher, both stages, and till notification

**Server**

Add to `seerbit.virtual.account`:

| Method | Responsibility |
| --- | --- |
| `_seerbit_va_owner()` | Resolve `partner_id` to an `hr.employee` and a `res.users`; return empty when neither stands behind the partner |
| `_seerbit_open_sessions(employee, user)` | Step 1 of Stage 1: open sessions of `company_id` on which the owner is a cashier or the session user |
| `_find_open_session_seerbit_line(amount)` | Step 2 of Stage 1: attributed draft orders within those sessions |
| `_find_cashier_seerbit_line(amount)` | Stage 2 search |
| `_settle_pos_payment(payment, reference)` | Apply the field mapping in section 4.5, publish the bus event, post to the chatter |
| `_seerbit_reference_already_used(reference)` | Idempotency check across `account.payment` and `pos.payment` |

Amend both callers so that the Virtual Account credit path becomes the fallback:

- `models/virtual_account.py`, `_fetch_and_process_payments`
- `controllers/webhook.py`, Section 3

**Client**

- Add a Point of Sale asset subscribing to `seerbit_pos_va_settled`.
- On receipt, if the referenced order is loaded, set the line to `done` and run the existing completion path.
- Leave the Firestore listener unchanged; terminal payments are unaffected.

**Configuration**

- `bus_notify.py` gains a helper for the new notification type, addressed to the session or employee rather than broadcast.
- `__manifest__.py` registers the new Point of Sale asset.

### Phase 2: refinement

- Restrict Stage 2 by an age window, for example orders from the current and previous session only.
- Support partial settlement, matching against the outstanding balance rather than the full line amount.
- Add a per company setting to enable or disable cashier Virtual Account settlement.
- Display the originating Virtual Account on the settled line in the backend order view.

---

## 6. Files affected in Phase 1

| File | Change |
| --- | --- |
| `models/virtual_account.py` | Matcher, settlement, idempotency; gate the credit path |
| `controllers/webhook.py` | Invoke the matcher before `_process_seerbit_va_payment` |
| `bus_notify.py` | Notification helper for `seerbit_pos_va_settled` |
| `static/src/js/pos_va_listener.js` | New Point of Sale listener |
| `__manifest__.py` | Register the listener under `point_of_sale._assets_pos` |
| `models/pos_payment.py` | Optional, only if a stored link to the Virtual Account is required |

The Firestore payload is unchanged.

---

## 7. Accounting outcomes

| Outcome | `account.payment` against the Virtual Account partner | Point of Sale journal |
| --- | --- | --- |
| Stage 1 match | None | Posted at session close through the Seerbit payment method |
| Stage 2 match | None | Already posted, or posted at session close |
| Fallback | Created, as at present | Not applicable |

The idempotency check in section 4.6 must execute before any branch, so that a credit already booked through the fallback is never settled a second time on a payment line.

---

## 8. Risks and controls

| Risk | Control |
| --- | --- |
| Two Seerbit lines of identical amount for one cashier | Deterministic ordering in section 4.4, with a warning recorded on the Virtual Account |
| Stage 2 matches an unrelated historical order of the same value | Restrict to lines with no transaction reference; apply the Phase 2 age window; Stage 2 makes no accounting entry, so the impact is limited to a misattributed reference |
| Two cashiers sharing one till, each holding a Seerbit line of the same amount | Both stages require the order to be attributed to the resolved owner through `employee_id`, so the other cashier's line is never a candidate |
| Employee lists left empty on `pos.config`, admitting every employee | Configuration permissions never qualify an owner on their own; Stage 1 requires an open session reached through the employee lists or `user_id`, and an order attributed to the owner within it |
| Cashier permitted on several configurations | Stage 1 is confined to open sessions of `va.company_id` and to orders attributed to that cashier; Stage 2 is confined to orders attributed to that cashier |
| Order not yet synchronised from an offline till | No candidate is found and the credit follows the fallback; the reference remains available for later attribution |
| Firestore and the Virtual Account webhook both report the same payment | Idempotency on `transaction_id` and `payment_ref_no` |
| Till offline when the bus event is published | The line is already `done` in the database; the till reflects it on the next synchronisation |
| `pos_hr` not installed | Both the session association and the order attribution fall back to `pos.session.user_id` |
| Employee with a Virtual Account who has never worked a till | No open session is associated and no order is attributed, so neither stage matches and the credit follows the fallback |
| Tips or partial payments | Not matched in Phase 1, since the comparison is on the exact amount; deferred to Phase 2 |

---

## 9. Acceptance criteria

1. A transfer into a cashier's Virtual Account, while a Seerbit line of that exact amount is waiting on an order attributed to that cashier in an open session, sets the line to `done`, allows the order to be validated, and creates no `account.payment` against the cashier.
2. Where two cashiers share an open till and both hold a Seerbit line of the exact amount, the credit settles only the line on the order attributed to the Virtual Account owner.
3. Where no open session holds a matching attributed line, the same transfer records the transaction reference on the cashier's Seerbit line of that exact amount, without creating accounting entries and without revalidating the order.
4. A transfer into the Virtual Account of an employee who has no associated open session and no attributed Point of Sale order follows the Virtual Account credit path, whether or not the employee lists on `pos.config` are empty.
5. A transfer into a customer Virtual Account continues to create an inbound payment and reconcile invoices in FIFO order.
6. A repeated webhook or cron pass for the same Seerbit reference produces no second payment and no second settlement.
7. Terminal payments settled through Firestore continue to operate unchanged where no Virtual Account is involved.
8. Where several candidate lines exist, the selected line and the candidate count are recorded on the Virtual Account chatter.

---

## 10. Out of scope

- Changes to how Virtual Accounts are provisioned for cashiers as opposed to customers.
- Replacement of Firestore for terminal payments.
- Multi currency tills.
- Refunds and reversals of lines settled from a Virtual Account.

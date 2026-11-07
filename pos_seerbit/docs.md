

# **PRD: SeerBit Virtual Account Payments on Odoo Point of Sale**

| Product Manager  | Ayomide Afolabi |
| :---- | :---- |
| Engineering Lead/Team Lead | Franklin Okeh  |
| Designer | OluwaTobi Ogunyinka  |
| Approvers/Sign-Off | Babajide Shoyebo  |

## **Overview**

This project aims to improve the existing **Odoo Point of Sale (POS) payment integration with Seerbit POS** by introducing a seamless Static transfer-payment flow that automatically links the customer's payment to the corresponding Odoo order.

Currently, the Odoo POS integration allows cashiers to select a SeerBit as a payment method, push the transaction amount directly to the Seerbit POS, and receive the transaction response back in Odoo. For card payments, this creates a seamless experience because the cashier does not need to manually re-enter or validate the transaction after payment. Once the transaction is successful, the Odoo order is automatically updated to **Paid** and the Seerbit transaction reference is returned for reconciliation.

However, the current Dynamic Transfer payment flow introduces additional friction. When Transfer is selected, an account number is generated, and the customer is expected to transfer the exact amount. If the customer pays an amount different from the expected amount, the payment may be rejected/reversed, preventing the order from being completed.

There is also an existing Odoo Accounting integration where cashiers are created as customers and assigned fixed accounts. These accounts can be mapped to invoices, allowing payments to automatically clear invoice balances. However, the current process requires the cashier to generate an invoice against the order, resulting in multiple actions before the transaction can be fully completed which takes a while 

The proposed solution is to **retain the reconciliation benefits of the existing accounting flow while removing additional cashier actions**. The system should automatically associate the transfer payment with the originating Odoo order, update the order with the Seerbit payment reference, and complete the payment/reconciliation process without requiring the cashier to manually generate or check an invoice.

## **Problem**

The current transfer payment flow creates friction for both customers and cashiers.

When a cashier selects **Transfer** from the POS after pushing the transaction from odoo, an account number is generated for the transaction. The customer is expected to transfer the exact amount displayed. In real-world transactions, customers may transfer more or less than the expected amount.

When the transferred amount does not exactly match the expected amount, the system may reject or reverse the transaction. This prevents the order from being completed even though funds have been received.

The current workaround through the accounting module also creates operational friction. Cashiers are created as customers and assigned fixed accounts. These accounts are mapped to invoices, and when an invoice is generated against an order, the payment can automatically clear the invoice balance.

However, this means the cashier may need to perform additional actions, including:

1. Validate/close the Odoo POS order.   
2. Check or generate an invoice against the order.  

This creates unnecessary long processing time at the point of sale.

### **Customer Pain Point**

Customers expect to be able to transfer the required amount without their transaction being rejected simply because the amount transferred differs from the original amount.

### **Cashier Pain Point**

Cashiers need to perform additional steps to complete and reconcile a transfer of payment, increasing transaction completion time and introducing opportunities for human error.

### **Business Pain Point**

The business needs payment-to-order mapping and transaction references for reconciliation, but the current implementation achieves this at the expense of checkout speed and operational efficiency.

## **Objectives**

This project succeeds if:

* **Automate Payment-to-Order Mapping:** Automatically associate a successful SeerBit Static transfer payment with the originating Odoo POS order.  
* **Automate Reconciliation:** Automatically return the SeerBit transaction in reference to Odoo and associate it with the relevant order/payment record for easy reconciliation.  
* **Reduce Cashier Actions:** Remove the requirement for cashiers to manually check or generate an invoice after completing a transfer of payment.  
* **Improve Checkout Speed:** Reduce the number of steps required to complete a transfer transaction and ensure the order is automatically updated once payment is confirmed.

## **Constraints**

Known roadblock

- Bank transfers land on bank timelines, not instantly like a card tap — the POS UI needs a genuine “awaiting transfer” pending state, not just an instant success/fail.  
* A merchant can have multiple VAs across branches or tills, so configuration has to map VA → POS/branch, not assume one VA per merchant.  
* Odoo POS and Odoo Accounting don't currently share a live “order awaiting payment” bridge — the exact matching key (unique VA per order vs. VA \+ amount \+ time window) still needs to be scoped with engineering.  
* Must not disrupt the existing Accounting-side VA reconciliation already running in production.

## **Persona/Stakeholders/Target User**

The people this feature touches, cashier first:

| POS Cashier (key persona) | Operates the till; selects “Virtual Account / Transfer” as the payment method, Initiates payment from Odoo POS and needs to complete customer orders quickly with minimal manual intervention. |
| :---- | :---- |
| **Merchant Admin / Finance Owner** | Configures which SeerBit virtual account(s) are attached to which POS branch or till, and monitors reconciliation across Accounting and POS. |
| **Customer** | Makes payment for an order through transfer and expects the payment to be completed without unnecessary rejection. |
| **Merchant Operation /Branch Manager**  | Requires reliable transaction processing and visibility into completed orders and payments. |

## **Use Cases**

Where this shows up in a real shift:

### **Scenario 1**

A merchant admin, from Odoo Accounting, attaches an existing SeerBit virtual account to a specific POS configuration/branch. From that point, “Virtual Account” appears as a payment option at that till.  
A customer at checkout chooses to pay by transfer. The cashier selects “Virtual Account” on the order, the VA number is shown, the customer transfers from their bank app, and the order flips to paid automatically once SeerBit's webhook confirms the credit — no manual check needed

A transfer arrives that doesn't cleanly match an open order — wrong amount, or the order it was meant for was voided in the meantime. The system flags it as unmatched rather than silently   
attaching it to the wrong order and surfaces it for manual review.

## **User Flows**  **Flow 1: Configure Seerbit Transfer Payment Method**

This Allows the merchant to configure Seerbit Transfer as a payment method in Odoo POS and associate it with a static account that will be used for receiving transfer payments.

The static account should be generated and associated with the payment method during configuration rather than generating a new account for every individual transaction.

### **Step 1: Create Payment Method**

* The merchant/admin navigates to:  
* **Odoo → Point of Sale → Configuration → Payment Methods**  
* The user selects **New** to create a payment method.

### **Step 2: Configure Payment Method**

* The user provides/configures the required payment method information:

| Field | Requirement |
| ----- | ----- |
| **Method** | SeerBit Transfer |
| **Online Payment** | Remains unchecked/not required for this flow |
| **Identify Customer** | Based on existing Odoo configuration |
| **Journal** | SeerBit |
| **Outstanding Account** | Configured Seerbit outstanding account |
| **Intermediary Account** | Optional, based on existing configuration |
| **Company** | Merchant's company |
| **Integration** **Type**  | **Transfer** **Static/dynamic** |

### **Step 3: Select Transfer Integration**

* When the administrator selects:  
* **Integration → Transfer**  
* **Type → Dynamic or static**   
* **If Dynamic, then no Generate button will be visible, at the point of sale is when an account will be generated for a specific order**   
* If Static The system should recognize that this payment method requires a SeerBit transfer account.  
* Once transfer is selected a Generate VA button should be visible and then selecting it create a static virtual account for the configured payment method   
* Once transfer is selected, user should be able to fetch static account linked to that public key and match any of the account to that Point of Sale   
* Associate the static account with the payment method.   
* Display the generated account details within the payment method configuration.   
* Make the payment method available for use on the configured Odoo POS.   
* Once successfully generated, the configuration should display the account associated with the payment method.  
* For example:

| Configuration | Value |
| ----- | ----- |
| Payment Method | SeerBit Transfer |
| Integration | Transfer |
| Static Account Number | XXXXXXXXXX |
| Account Name | Merchant Account Name |
| Bank | SeerBit/Configured Bank |

The account should be **static**, meaning the same account remains associated with the configured payment method rather than generating a new account every time a customer initiates a transfer.

# **Flow 2: Payment Confirmation on Odoo POS**

Allow a cashier to confirm an Odoo order by selecting the corresponding payment from a list of payments received into the merchant's configured static SeerBit account.

Because the account is static and can receive payments from multiple customers simultaneously, the system must provide the cashier with visibility of incoming payments and allow them to identify and select the payment belonging to the current order.

Once the cashier selects the correct payment, the system should automatically confirm the payment against the Odoo order, mark the order as **Paid**, and save the SeerBit transaction reference for reconciliation.

## **Step 1: Cashier Creates the Order**

The cashier creates the order in Odoo POS.

For example:

**Order Amount:** ₦50,000  
 **Order Created:** 12:30 PM  
 **Order ID:** ORD-00125

The cashier proceeds to the payment screen and selects: **SeerBit Transfer**

**If It's a Dynamic Flow that was selected**  

## **Step 2: POS Displays the Dynamic Account**

Because the payment method has already been configured with a static SeerBit account, the POS displays the dynamic account details linked to that order 

Example:

**Transfer to:**

* **Account Number:** 1234567890   
* **Account Name:** Merchant Name   
* **Bank:** SeerBit/Configured Bank   
* **Amount:** ₦50,000

The customer is expected to the exact amount the dynamic account is expecting with a count down because the account will expire 

# **Step 3: Incoming Transfers matches orders with automatic matching order**

### **Important**

The account is **unique to the order**.

Only one customer can transfer money into that account at the same time with the exact amount expected 

Once the transfer payment is received, the system should automatically check with the reference generated if the status has been updated and update the order on Odoo with all details neede 

**If It's a Static Flow that was selected**  

## **Step 2: POS Displays the Static Account**

Because the payment method has already been configured with a static SeerBit account, the POS displays the account details to the cashier/customer.

Example:

**Transfer to:**

* **Account Number:** 1234567890   
* **Account Name:** Merchant Name   
* **Bank:** SeerBit/Configured Bank   
* **Amount:** ₦50,000 

The customer makes the transfer into this account.

### **Important**

The account is **not unique to the order**.

Multiple customers can transfer money into the same account at the same time.

Therefore:

**Static Account → Multiple Incoming Payments → Cashier identifies the payment belonging to the order**

# **Step 3: Incoming Transfers matches orders** 

Once the transfer payment is received, the system should automatically check incoming payments associated with the configured static account.

The system should attempt to match the incoming payment to the current Odoo order using:

* **Expected order amount**  
* **Payment received date/time**  
* **Order creation date/time**  
* **Payment status**  
* **Payment/reference information**

The primary matching condition is that the **incoming payment amount matches the order amount** and the payment was received within the relevant time period starting from when the order was created.

### **Step 4: Automatically Confirm the Order**

If an incoming payment matches the order based on the configured matching rules, the system should automatically:

1. Associate the SeerBit payment with the Odoo order.  
2. Store the SeerBit transaction reference against the payment/order.  
3. Record the actual amount received.  
4. Update the payment status.  
5. Mark the Odoo order as **Paid**.  
6. Complete the transaction without requiring cashier intervention.

The cashier should not need to manually select or revalidate the payment.

**Example:**

Order:

* Order Amount: ₦50,000  
* Order Created: 12:30 PM

Incoming payment:

* Amount: ₦50,000  
* Payment Time: 12:34 PM  
* Status: Successful  
* Reference: SB123456

**Result:** The system identifies the matching payment and automatically marks **ORD-00125 as Paid**.

### **Step 5: Manual Payment Matching When No Automatic Match Is Found**

If the system receives incoming payments but **cannot identify a payment that matches the order amount and matching conditions**, the order should remain unpaid.

The cashier should then have the option to:

**View/Select Incoming Payment**

This allows the cashier to manually identify the payment belonging to the current order.

The system should retrieve recent **successful and/or pending incoming payments that have not already been mapped to another order** and associated with the configured static account.

The payment list should be filtered to the relevant period, with the primary consideration being payments received **from the time the current order was created up to the current time**.

## **Payment Selection View**

The POS should display a payment list similar to:

| Payment date & Time | Customer/Payer Name | Amount | Reference | Status | Action |
| ----- | ----- | ----- | ----- | ----- | ----- |
| 12:34 PM | John Doe | ₦50,000 | SB123456 | Successful | **Select** |
| 12:36 PM | Mary James | ₦25,000 | SB123457 | Successful | **Select** |
| 12:38 PM | Ade Stores | ₦50,500 | SB123458 | Successful | **Select** |

The cashier should be able to identify the customer's payment using information such as:

* **Payer name**   
* **Amount**   
* **Payment date/time**   
* **SeerBit transaction reference**   
* **Payment status** 

The **payer's name** is particularly important because the cashier can use the customer's name to identify the payment when multiple payments have been received.

# **Step 4: Cashier Selects the Payment**

The cashier identifies the payment belonging to the current order and clicks:

**Select Payment**

For example:

**John Doe — ₦50,000 — 12:34 PM**

The system should then display a confirmation state:

**Payment Selected**

* Order: ORD-00125   
* Order Amount: ₦50,000   
* Customer/Payer: John Doe   
* Amount Received: ₦50,000   
* SeerBit Reference: SB123456 

The cashier confirms the selection.

# **Step 5: System Confirms the Order**

Once the cashier selects the payment, the system should automatically:

1. Associate the selected SeerBit payment with the Odoo order.   
2. Store the SeerBit transaction reference against the payment/order.   
3. Record the actual amount received.   
4. Update the payment status.   
5. Mark the Odoo order as **Paid**, where the payment satisfies the configured payment rules.   
6. Complete the transaction without having to revalidate the order 

## **User Stories**

| Feature | Story | Inputs | Acceptance Criteria |
| :---- | :---- | :---- | :---- |
| SeerBit Transfer Payment Method Configuration | As an **Odoo POS administrator**, I want to configure SeerBit Transfer as a payment method so that cashiers can use transfer as a payment option on the POS. | Payment Method Name, Journal, Outstanding Account, Intermediary Account (optional), Company, POS, Integration Type | Given the administrator is configuring a payment method, when **Transfer** is selected as the integration,  then the payment method should be successfully configured and available on the selected POS. |
| Generate Static Account | As an **Odoo POS Administrator**, I want a static account to be generated for the configured SeerBit Transfer payment method so that multiple customers can make payments into the same account. | Merchant ID, Business Account ID, Payment Method ID,public and private key,  | **Given** the SeerBit Transfer payment method has been configured successfully **When** the administrator selects **Generate VA** **Then** the system should generate/retrieve a static SeerBit virtual account and associate it with the configured payment method. |
| Display Static Account | As an **Odoo POS Administrator**, I want to see the generated static account in the payment method configuration so that I can confirm the account has been successfully configured. | Payment Method ID, Static Account Number, Account Name, Bank Name | **Given** a static SeerBit account has been successfully generated and associated with the payment method **When** the administrator views the payment method configuration **Then** the system should display the static account number, account name, and bank name against the payment method, and the same account should remain associated with the payment method without generating a new account for each order.  |
| Select Transfer Payment | As a **Cashier**, I want to select SeerBit Transfer as the payment method for an order so that the customer can pay via bank transfer. | Odoo Order ID, Order Amount, Payment Method ID, POS ID, Cashier ID | **Given** the cashier has selected SeerBit Transfer for an order**When** the payment is initiated**Then** the POS should retrieve the static SeerBit account configured for the payment method and place the order in a **Pending Payment** state while awaiting the incoming transfer.  |
| Display Static Account | As an **Odoo POS Administrator**, I want to see the generated static account in the payment method configuration so that I can confirm the account has been successfully configured. | Payment Method ID, Static Account Number, Account Name, Bank Name | **Given** a static SeerBit account has been generated for the payment method**When** the administrator views the payment method configuration**Then** the system should display the account number, account name, and bank name, and keep the same account associated with the payment method without regenerating it for each order.  |
| Select Transfer Payment | As a **Cashier**, I want to select SeerBit Transfer as the payment method for an order so that the customer can pay via bank transfer. | Odoo Order ID, Order Amount, Payment Method ID, POS ID, Cashier ID | **Given** the cashier selects SeerBit Transfer for an order **When** the payment is initiated **Then** the POS should retrieve the static account configured for the payment method and display accordingly   |
| Display Payment Details | As a **Cashier**, I want to see the static account details on the payment screen so that I can provide the customer with the information required to make the transfer. | Order ID, Order Amount, Static Account Number, Account Name, Bank Name | **Given** SeerBit Transfer has been selected as the payment method **When** the payment screen is displayed **Then** the POS should display the amount due, static account number, account name, and bank name to the cashier. |
| Create Payment Session | As a **Cashier**, I want the transfer payment to be associated with the current Odoo order so that incoming payments can subsequently be considered for that order. | Odoo Order ID, Order Creation Timestamp, Order Amount, Static Account ID, POS ID | **Given** a SeerBit Transfer payment has been initiated for an Odoo order **When** the payment session is created **Then** the system should associate the session with the current order and record the order creation timestamp and payment initiation time for matching incoming payments. |
| Automatic Confirmation  | **As a** cashier using Odoo POS,**I want** SeerBit to automatically match an incoming transfer to the current order based on the order amount and payment timing,**so that** the order is automatically marked as **Paid** without requiring me to manually select or revalidate the payment.  | Odoo Order ID Order Amount Order Creation Date & Time SeerBit Static Account Number Incoming Payment Amount Incoming Payment Date & Time Incoming Payment Status SeerBit Transaction Reference Payer Name  | **Given** an Odoo POS order has been created with an expected amount of ₦50,000 **And** the order was created at 12:30 PM **And** the customer makes a successful transfer of ₦50,000 to the configured SeerBit static account **And** the payment is received after the order was created **When** the system detects the incoming payment **And** the payment amount matches the order amount **Then** the system should automatically associate the payment with the Odoo order **And** store the SeerBit transaction reference against the order **And** record the actual amount received **And** update the payment status **And** mark the Odoo order as **Paid** **And** the cashier should not be required to manually select or revalidate the payment. |
| Retrieve Incoming Payments | As a **Cashier**, I want to view payments received into the configured static account so that I can identify the payment made by the customer. | Static Account Number/ID, Order Creation Timestamp, Current Timestamp, Payment Status | **Given** an Odoo POS order has been created with an expected amount of ₦50,000 **When** the system checks the configured SeerBit static account **And** no incoming payment matching the order amount is found **Then** the order should remain unpaid **And** the cashier should be able to use **View/Select Incoming Payment** to search for and select an applicable incoming payment. |
| Filter Payments by Time | As a **Cashier**, I want to see payments received from the time the current order was created so that I do not have to search through older transactions. | Order Creation Timestamp, Current Timestamp, Static Account ID | **Given** a payment session has been created for the current order **When** the cashier views incoming payments **Then** the system should display payments received into the configured static account from the order creation timestamp up to the current timestamp. |
| Display Payment Information | As a **Cashier**, I want to see information about each incoming payment so that I can determine which payment belongs to the current customer/order. | Payment Reference, Payer Name, Amount, Payment Date/Time, Payment Status, Static Account | **Given** incoming payments are available for the configured static account **When** the cashier views the payment list **Then** each payment should display the payer name, amount, payment date/time, SeerBit transaction reference, and payment status. |
| Refresh Incoming Payments | As a **Cashier**, I want to refresh the incoming payment list so that newly received payments become visible without restarting the order. | Static Account ID, Current Timestamp, Order ID | **Given** the cashier is viewing incoming payments for the current order **When** the cashier refreshes the payment list **Then** the system should retrieve and display the latest eligible payments received into the static account from the order creation time up to the current timestamp. |
| Select Incoming Payment | As a **Cashier**, I want to select an incoming payment so that I can associate it with the current Odoo order. | Odoo Order ID, Selected Payment ID, Payment Amount, Payer Name, Payment Reference | **Given** eligible incoming payments are displayed for the current order **When** the cashier selects an incoming payment **Then** the system should associate the selected payment with the current Odoo order using the payment details and reference. |
| Review Selected Payment | As a **Cashier**, I want to review the selected payment before confirmation so that I can ensure I have selected the correct customer's payment. | Order ID, Order Amount, Payment ID, Payer Name, Payment Amount, Payment Timestamp, SeerBit Reference | **Given** SeerBit Transfer has been selected for the current Odoo order **When** the payment session is created **Then** the system should associate the payment session with the order and record the order creation timestamp and payment initiation time for matching incoming payments. |
| Automatically Mark Order as Paid | As a **Cashier**, I want the order to automatically become Paid after selecting the correct payment so that I do not have to manually validate the transaction again. | Odoo Order ID, Selected Payment ID, Payment Amount, Payment Status | **Given** a selected payment is successful and matches the order amount **When** the payment is confirmed **Then** the system should automatically associate the payment with the Odoo order and mark the order as **Paid** without requiring further manual validation. |
| Store SeerBit Reference | As a **Finance/Operations User**, I want the SeerBit transaction reference stored against the Odoo order so that the transaction can be easily identified during reconciliation. | Odoo Order ID, SeerBit Transaction Reference, Payment ID | **Given** a payment has been successfully associated with an Odoo order **When** the payment is confirmed **Then** the system should automatically store the SeerBit transaction reference against the Odoo payment/order without requiring the cashier to enter it manually. |
| Validate Exact Payment | As a **Cashier**, I want the system to identify when the payment received matches the order amount so that I can confirm the payment. | Order Amount, Payment Amount, Payment ID, Order ID | **Given** an incoming payment is available for the current order **When** the Payment Amount equals the Order Amount **Then** the payment should be marked as eligible for confirmation, and after confirmation, the order should be marked as **Paid**. |
| Prevent Duplicate Payment Selection | As a **Cashier**, I want payments that have already been used to be identified so that the same payment cannot be applied to multiple orders. | Payment ID, SeerBit Transaction Reference, Payment Status, Order ID | **Given** an incoming payment has already been successfully associated with an order **When** the cashier views incoming payments for another order **Then** the payment should be identified as already used and should not be available for confirmation against another order. |
| Handle Payment Not Found | As a **Cashier**, I want to refresh the payment list when a customer's payment is not immediately visible so that I can continue checking for the payment. | Static Account ID, Order ID, Order Creation Timestamp, Current Timestamp | **Given** no eligible payment is currently visible for the order **When** the cashier refreshes the payment list **Then** the system should retrieve and display the latest eligible payments received into the static account without cancelling or recreating the order. |
| Handle Payment Status | As a **Cashier**, I want to see the status of incoming payments so that I know whether a payment can be selected. | Payment ID, Payment Reference, Payment Status | **Given** no eligible payment is currently visible for the order **When** the cashier refreshes the payment list **Then** the system should retrieve and display the latest eligible payments received into the static account without cancelling or recreating the order. |

## **Edge Cases & "Unhappy" Paths**

Scenarios the design has to account for:

* **No payment received:** The order remains in *Awaiting Payment* status, and the cashier can refresh the incoming payment list.   
* **Payment received before the order was created:** The payment should be presented as an eligible payment for the current order.   
* **Multiple payments received:** All eligible payments received after the order creation time should be displayed so the cashier can identify the correct payment.   
* **Multiple customers pay the same amount:** The system should not automatically match based on amount alone. The cashier must identify the correct payment using payer name, amount, time, and transaction reference.    
* **Payment is pending:** A pending payment should not be available for final confirmation until SeerBit confirms it as successful.   
* **Payment fails or is reversed:** The payment should not be available for selection/confirmation.  
* **Payer name is unavailable:** The payment should still be displayed using other available information such as amount, payment time, and transaction reference.   
* **Payment is not immediately visible:** The cashier should be able to refresh the payment list without cancelling or recreating the order.   
* **Payment is already used:** A payment that has already been linked to another order should not be selectable again.   
* **The** cashier should be able to cancel the selection before final confirmation and select another payment.   
* **Payment becomes unavailable after selection:** The system should revalidate the payment before confirmation and prevent the order from being confirmed if the payment is no longer eligible.   
* **Duplicate confirmation attempt:** The same payment should not be applied to an order more than once, even if the confirmation request is submitted multiple times. 

## **Security & Compliance Requirements**

* **Authentication:** All communication between Odoo and SeerBit APIs must use the required authentication mechanism. Unauthenticated requests must be rejected.   
* **Payment Verification:** Odoo must verify the payment status from the backend/SeerBit before allowing a payment to be associated with an order.   
* **Duplicate Payment Prevention:** A SeerBit payment/reference can only be associated with one Odoo order. The system must prevent the same payment from being used to complete multiple orders.   
* **Payment Reference Integrity:** The SeerBit transaction reference and payment ID must be retrieved from the payment service and must not be editable by the cashier.   
* **Transaction Idempotency:** Repeated payment confirmation requests must not create duplicate payment records or confirm the same order multiple times.   
* **Concurrent Payment Selection:** The system must prevent two cashiers from successfully assigning the same incoming payment to different orders at the same time.   
* **Data Protection:** Only information required to identify and process the payment should be displayed to the cashier. Sensitive financial or payment information should not be unnecessarily exposed or stored.   
* **API Security:** API credentials, authentication keys, and other integration secrets must not be exposed in the Odoo POS frontend or client-side code.   
* **Secure Communication:** Communication between Odoo and SeerBit must use encrypted communication such as HTTPS/TLS.   
* **Error Handling:** Security-sensitive API errors should not expose credentials, internal system details, database information, or other sensitive implementation details to the cashier. 

## **Success Metrics**

| Metric | Type | Baseline | Target | Timeline |
| ----- | ----- | ----- | ----- | ----- |
| % of transfer payments completed using the new automated flow | Adoption / Activation | 0% at launch | ≥80% of eligible transfer payments | Within 3 months of launch |
| Successful completion rate for valid transfer payments | Lagging / Conversion | Establish during first 2 weeks | ≥98% | Within 3 months |
| Accuracy of linking incoming payments to the correct Odoo order | Lagging / Accuracy | Establish during first 2 weeks | ≥99% | Within 3 months |
| Average time to complete a transfer payment at checkout | Lagging / Efficiency | Measure current flow before launch | ≥30% reduction from baseline | Within 3 months |
| Manual payment validation after correct payment selection | Automation | Current flow requires manual validation | 0% | From launch |
| Manual entry of SeerBit transaction references | Automation | Current flow requires manual entry | 0% | From launch |
| Duplicate payment associations | Risk / Accuracy | Establish current rate before launch | 0 | From launch |
| Valid incoming payments successfully associated with an order | Lagging / Conversion | Establish during first 2 weeks | ≥98% | Within 3 months |
| Valid selected payments successfully confirming the order as Paid | Lagging / Conversion | Establish during first 2 weeks | ≥99% | Within 3 months |
| Payment failures caused by amount mismatch | Lagging / Error Reduction | Measure current transfer-flow rate before launch | Reduction from baseline | Within 3 months |
| Reconciliation accuracy for confirmed transfer payments | Lagging / Reconciliation | Establish during first 2 weeks | ≥99% | Within 3 months |

* 

## **Non-Functional Requirements**

* **Performance:** Payment amount validation should return the match result within 5 seconds.  
* **Reliability:** The system must validate against the latest successful payment data and must not mark an order as Paid when the amount does not match.  
* **Scalability:** The validation should support multiple Odoo POS orders and incoming payments being processed concurrently.  
* **Security:** Only authorised POS users should be able to view and validate incoming payment details.  
* **Compliance:** Payment and transaction data must be handled in line with applicable payment and data-protection requirements.  
* **Observability:** Validation attempts, match results, failures, and associated Payment IDs should be logged for troubleshooting and reconciliation.


## **System Architecture Requirements**

* **Systems:** Odoo POS, SeerBit Virtual Account Service, SeerBit Payment/Transaction Service.  
* **Data Flow:** Odoo sends the static account and order details → Virtual Account Service provides incoming payment data → Odoo compares the Payment Amount with the Order Amount.  
* **Dependencies:** Odoo order/payment session, static virtual account, and successful payment status.  
* **Protocol:** REST API over HTTPS.  
* **Downstream:** Validated payment is returned to Odoo POS for confirmation and order update.


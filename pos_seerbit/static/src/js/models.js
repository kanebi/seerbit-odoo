/** @odoo-module **/

import { register_payment_method } from "@point_of_sale/app/store/pos_store";
import { PosStore } from "@point_of_sale/app/store/pos_store";
import { PosPayment } from "@point_of_sale/app/models/pos_payment";
import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { patch } from "@web/core/utils/patch";
import PaymentSeerbit from "./payment_seerbit";

patch(PosPayment.prototype, {
    setPaymentStatus(value) {
        return this.set_payment_status ? this.set_payment_status(value) : (this.payment_status = value);
    },
    getPaymentStatus() {
        return this.get_payment_status ? this.get_payment_status() : this.payment_status;
    },
    getAmount() {
        return this.get_amount ? this.get_amount() : this.amount;
    },
    setAmount(value) {
        return this.set_amount ? this.set_amount(value) : (this.amount = value);
    },
    setReceiptInfo(value) {
        return this.set_receipt_info ? this.set_receipt_info(value) : (this.ticket = (this.ticket || "") + value);
    },
    setCashierReceipt(value) {
        return this.set_cashier_receipt ? this.set_cashier_receipt(value) : (this.cashier_receipt = value);
    },
});

patch(PosOrder.prototype, {
    removePaymentline(line) {
        return this.remove_paymentline ? this.remove_paymentline(line) : false;
    },
    getSelectedPaymentline() {
        return this.get_selected_paymentline ? this.get_selected_paymentline() : this.selected_paymentline;
    },
});

patch(PosStore.prototype, {
    getOrder() {
        return this.get_order ? this.get_order() : null;
    },
});

register_payment_method("seerbit", PaymentSeerbit);

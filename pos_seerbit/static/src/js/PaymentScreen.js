/** @odoo-module **/

import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { PaymentScreenPaymentLines } from "@point_of_sale/app/screens/payment_screen/payment_lines/payment_lines";
import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { SeerbitVaPaymentWidget } from "./seerbit_va_payment_widget";
import { SeerbitVaModalPopup } from "./seerbit_va_modal_popup";
import { patch } from "@web/core/utils/patch";
import { onMounted, onWillUnmount } from "@odoo/owl";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { _t } from "@web/core/l10n/translation";

PaymentScreenPaymentLines.components = {
    ...PaymentScreenPaymentLines.components,
    SeerbitVaPaymentWidget,
    SeerbitVaModalPopup,
};

patch(PaymentScreenPaymentLines.prototype, {
    toggleSeerbitAccordion(line) {
        line.seerbit_expanded = !line.seerbit_expanded;
    },
    openSeerbitModal(line) {
        this.dialog.add(SeerbitVaModalPopup, {
            paymentLine: line,
            pos: this.pos,
            validateOrder: () => this.props.validateOrder?.(),
        });
    },
    async selectLine(paymentline) {
        await super.selectLine(paymentline);
        if (paymentline?.payment_method_id?.payment_method_type === "transfer") {
            const isMatched = Boolean(
                paymentline.seerbit_is_matched ||
                paymentline.seerbit_va_transaction_id ||
                paymentline.seerbit_transaction_ref ||
                paymentline.payment_status === "done"
            );
            if (!isMatched) {
                this.openSeerbitModal(paymentline);
            }
        }
    },
});

patch(PosOrder.prototype, {
    canBeValidated() {
        const hasUnmatchedTransfer = (this.payment_ids || []).some(
            (line) => line.payment_method_id?.payment_method_type === "transfer" &&
                      !line.seerbit_va_transaction_id &&
                      !line.seerbit_transaction_ref &&
                      !line.seerbit_is_matched &&
                      line.payment_status !== "done"
        );
        if (hasUnmatchedTransfer) {
            return false;
        }
        return super.canBeValidated();
    },
});

patch(PaymentScreen.prototype, {
    setup() {
        super.setup(...arguments);
        onMounted(() => {
            const pendingPaymentLine = this.pos.getPendingPaymentLine("seerbit");
            if (pendingPaymentLine) {
                pendingPaymentLine.setPaymentStatus("waitingSeerbit");
            }
        });

        onWillUnmount(() => {
            this.paymentLines.forEach((line) => {
                if (line.payment_method_id.use_payment_terminal === "seerbit") {
                    line.payment_method_id.payment_terminal?.close?.();
                }
            });
        });
    },

    async addNewPaymentLine(paymentMethod) {
        if (paymentMethod?.payment_method_type === "transfer") {
            const amountDue = this.currentOrder.getDefaultAmountDueToPayIn ? 
                this.currentOrder.getDefaultAmountDueToPayIn(paymentMethod) : 
                (this.currentOrder.get_due ? this.currentOrder.get_due() : this.currentOrder.totalDue);
            if (amountDue <= 1.0) {
                this.dialog.add(AlertDialog, {
                    title: _t("Amount Too Low"),
                    body: _t("Transfer payment amount must be greater than 1.00."),
                });
                return false;
            }
        }
        const res = await super.addNewPaymentLine(paymentMethod);
        if (res && paymentMethod?.payment_method_type === "transfer") {
            const selectedLine = this.selectedPaymentLine;
            if (selectedLine) {
                selectedLine.setPaymentStatus("waiting_transfer");
                selectedLine.seerbit_is_matched = false;
                selectedLine.seerbit_expanded = false;

                this.dialog.add(SeerbitVaModalPopup, {
                    paymentLine: selectedLine,
                    pos: this.pos,
                    validateOrder: () => this.validateOrder(),
                });
            }
        }
        return res;
    },

    async updateSelectedPaymentline(amount = false) {
        if (this.selectedPaymentLine?.payment_method_id?.payment_method_type === "transfer") {
            const hasGeneratedVa = Boolean(this.selectedPaymentLine.seerbit_dynamic_account_number);
            if (hasGeneratedVa && amount !== null && this.numberBuffer.get() !== null) {
                this.notification.add(
                    _t("Bank account already created for this amount. To change amount, remove this line and add it again."),
                    { type: "warning" }
                );
                return;
            }
        }
        return super.updateSelectedPaymentline(amount);
    },

    async validateOrder(isForceValidate = false) {
        const unmatchedTransfer = this.paymentLines.find(
            (line) => line.payment_method_id?.payment_method_type === "transfer" &&
                      !line.seerbit_va_transaction_id &&
                      !line.seerbit_transaction_ref &&
                      !line.seerbit_is_matched &&
                      line.payment_status !== "done"
        );
        if (unmatchedTransfer) {
            this.dialog.add(AlertDialog, {
                title: _t("Bank Transfer Unmatched"),
                body: _t("Cannot complete order: Bank transfer payment of %s has not been confirmed or matched yet. Please match the payment before validating.", this.env.utils.formatCurrency(unmatchedTransfer.getAmount())),
            });
            return false;
        }
        return super.validateOrder(isForceValidate);
    },

    async sendForceDone(line) {
        const payment_terminal = line.payment_method_id.payment_terminal;
        if (payment_terminal?.sendForceDone) {
            await payment_terminal.sendForceDone(line);
        }
        return super.sendForceDone(line);
    },

    paymentMethodImage(id) {
        const method = this.payment_methods_from_config.find((m) => m.id === id);
        if (method?.use_payment_terminal === "seerbit" || method?.payment_method_type === "transfer") {
            return "/pos_seerbit/static/description/icon.png";
        }
        return super.paymentMethodImage(id);
    },

    deletePaymentLine(uuid) {
        const line = this.paymentLines.find((paymentLine) => paymentLine.uuid === uuid);
        if (
            line &&
            ["waitingSeerbit", "errorSeerbit"].includes(line.getPaymentStatus()) &&
            line.payment_method_id.payment_terminal
        ) {
            line.setPaymentStatus("waitingCancel");
            line.payment_method_id.payment_terminal
                .sendPaymentCancel(this.currentOrder, uuid)
                .then(() => {
                    this.currentOrder.removePaymentline(line);
                    this.numberBuffer.reset();
                });
            return;
        }
        return super.deletePaymentLine(uuid);
    },
});

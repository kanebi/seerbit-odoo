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
    openSeerbitModal(line, options = {}) {
        if (!line || line._seerbit_modal_open) {
            return;
        }
        const order = this.pos?.getOrder ? this.pos.getOrder() : this.pos?.get_order?.();
        let balanceRequired = 0;
        const originalSettled = Number(line.seerbit_matched_amount || 0);

        if (line.seerbit_is_partially_matched) {
            // Calculate unpaid balance remaining on the order
            const currentDue = order?.get_due ? order.get_due() : (order?.totalDue !== undefined ? order.totalDue : 0);
            balanceRequired = Math.max(0, currentDue);

            // On open: update line to add balance required so total order tender is accounted for
            if (balanceRequired > 0.001) {
                const newAmount = originalSettled + balanceRequired;
                if (line.set_amount) {
                    line.set_amount(newAmount);
                } else if (line.setAmount) {
                    line.setAmount(newAmount);
                } else {
                    line.amount = newAmount;
                }
            }
        }

        line._seerbit_modal_open = true;
        this.dialog.add(SeerbitVaModalPopup, {
            paymentLine: line,
            pos: this.pos,
            autoGenerate: Boolean(options.autoGenerate),
            balanceRequired: balanceRequired,
            isReopeningPartial: Boolean(line.seerbit_is_partially_matched),
            validateOrder: () => {
                if (typeof this.pos?.validateOrder === "function") {
                    return this.pos.validateOrder();
                }
                if (typeof this.pos?._active_payment_screen?.validateOrder === "function") {
                    return this.pos._active_payment_screen.validateOrder();
                }
                if (typeof this.props.validateOrder === "function") {
                    return this.props.validateOrder();
                }
            },
        }, {
            onClose: () => {
                line._seerbit_modal_open = false;
                delete line._seerbit_modal;
                if (this.pos && this.pos._active_seerbit_modal?.paymentLine === line) {
                    this.pos._active_seerbit_modal = null;
                }
            }
        });
    },
    async selectLine(paymentline) {
        await super.selectLine(paymentline);
        if (paymentline?.payment_method_id?.payment_method_type === "transfer") {
            const isFullyMatched = Boolean(
                !paymentline.seerbit_is_partially_matched &&
                (paymentline.seerbit_is_matched || paymentline.payment_status === "done")
            );
            if (!isFullyMatched && !paymentline._seerbit_modal_open) {
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
        this.pos._active_payment_screen = this;
        this.pos.validateOrder = (isForce = false) => this.validateOrder(isForce);

        onMounted(() => {
            this.pos._active_payment_screen = this;
            this.pos.validateOrder = (isForce = false) => this.validateOrder(isForce);
            const pendingPaymentLine = this.pos.getPendingPaymentLine("seerbit");
            if (pendingPaymentLine) {
                if (pendingPaymentLine.set_payment_status) {
                    pendingPaymentLine.set_payment_status("waitingSeerbit");
                } else if (pendingPaymentLine.setPaymentStatus) {
                    pendingPaymentLine.setPaymentStatus("waitingSeerbit");
                }
            }
        });

        onWillUnmount(() => {
            if (this.pos._active_payment_screen === this) {
                this.pos._active_payment_screen = null;
            }
            if (this.pos.validateOrder) {
                delete this.pos.validateOrder;
            }
            this.paymentLines.forEach((line) => {
                if (line.payment_method_id.use_payment_terminal === "seerbit") {
                    line.payment_method_id.payment_terminal?.close?.();
                }
            });
        });
    },

    openSeerbitModal(line, options = {}) {
        if (!line || line._seerbit_modal_open) {
            return;
        }
        line._seerbit_modal_open = true;
        this.dialog.add(SeerbitVaModalPopup, {
            paymentLine: line,
            pos: this.pos,
            autoGenerate: Boolean(options.autoGenerate),
            validateOrder: () => this.validateOrder(),
        }, {
            onClose: () => {
                line._seerbit_modal_open = false;
                delete line._seerbit_modal;
                if (this.pos && this.pos._active_seerbit_modal?.paymentLine === line) {
                    this.pos._active_seerbit_modal = null;
                }
            }
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
                if (selectedLine.set_payment_status) {
                    selectedLine.set_payment_status("waiting_transfer");
                } else if (selectedLine.setPaymentStatus) {
                    selectedLine.setPaymentStatus("waiting_transfer");
                }
                selectedLine.seerbit_is_matched = false;
                selectedLine.seerbit_expanded = false;

                const isDynamic = paymentMethod?.seerbit_transfer_type === "dynamic";
                selectedLine.seerbit_auto_generate = isDynamic;
                selectedLine.seerbit_amount_changed = false;

                this.openSeerbitModal(selectedLine, { autoGenerate: isDynamic });
            }
        }
        return res;
    },

    async updateSelectedPaymentline(amount = false) {
        if (this.selectedPaymentLine?.payment_method_id?.payment_method_type === "transfer") {
            // Reset cached dynamic account details so a new account for the updated amount can be generated cleanly
            this.selectedPaymentLine.seerbit_dynamic_account_number = null;
            this.selectedPaymentLine.seerbit_dynamic_bank_name = null;
            this.selectedPaymentLine.seerbit_dynamic_account_name = null;
            this.selectedPaymentLine.seerbit_dynamic_va_id = null;
            this.selectedPaymentLine.seerbit_dynamic_reference = null;
            this.selectedPaymentLine.seerbit_dynamic_expires_at = null;
            this.selectedPaymentLine.seerbit_dynamic_amount = null;
            this.selectedPaymentLine.seerbit_amount_changed = true;
            this.selectedPaymentLine.seerbit_auto_generate = false;
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
            const transferAmt = unmatchedTransfer.get_amount ? unmatchedTransfer.get_amount() : (unmatchedTransfer.getAmount ? unmatchedTransfer.getAmount() : (unmatchedTransfer.amount || 0));
            this.dialog.add(AlertDialog, {
                title: _t("Bank Transfer Unmatched"),
                body: _t("Cannot complete order: Bank transfer payment of %s has not been confirmed or matched yet. Please match the payment before validating.", this.env.utils.formatCurrency(transferAmt)),
            });
            return false;
        }

        // Failsafe for overpayment in sessions without a cash payment method:
        // If session has no cash statement and order has change due from an overpaid transfer line,
        // cap the transfer line to the order balance so change is 0.0 and Odoo doesn't raise
        // "No cash statement found for this session. Unable to record returned cash."
        const hasCash = this.pos.config?.payment_method_ids?.some?.((pm) => pm.is_cash_count);
        if (!hasCash && this.currentOrder) {
            const change = this.currentOrder.get_change ? this.currentOrder.get_change() : 0;
            if (change > 0.001) {
                const transferLine = this.paymentLines.find(
                    (l) => l.payment_method_id?.payment_method_type === "transfer" && l.amount > 0
                );
                if (transferLine) {
                    const newAmount = Math.max(0, transferLine.amount - change);
                    if (transferLine.set_amount) {
                        transferLine.set_amount(newAmount);
                    } else if (transferLine.setAmount) {
                        transferLine.setAmount(newAmount);
                    } else {
                        transferLine.amount = newAmount;
                    }
                }
            }
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
        const status = line ? (line.get_payment_status ? line.get_payment_status() : (line.getPaymentStatus ? line.getPaymentStatus() : line.payment_status)) : null;
        if (
            line &&
            ["waitingSeerbit", "errorSeerbit"].includes(status) &&
            line.payment_method_id.payment_terminal
        ) {
            if (line.set_payment_status) {
                line.set_payment_status("waitingCancel");
            } else if (line.setPaymentStatus) {
                line.setPaymentStatus("waitingCancel");
            }
            line.payment_method_id.payment_terminal
                .sendPaymentCancel(this.currentOrder, uuid)
                .then(() => {
                    if (this.currentOrder.remove_paymentline) {
                        this.currentOrder.remove_paymentline(line);
                    } else if (this.currentOrder.removePaymentline) {
                        this.currentOrder.removePaymentline(line);
                    }
                    this.numberBuffer.reset();
                });
            return;
        }
        return super.deletePaymentLine(uuid);
    },
});

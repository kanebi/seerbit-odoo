/** @odoo-module **/

import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";

export const posSeerbitBusService = {
    dependencies: ["bus_service", "pos", "notification"],
    start(env, { bus_service, pos, notification }) {
        const getSessionId = () => {
            return (typeof odoo !== 'undefined' && odoo.pos_session_id) ||
                pos.session?.id ||
                pos.config?.current_session_id?.[0] ||
                pos.config?.current_session_id;
        };

        const sessionId = getSessionId();
        if (sessionId) {
            const channel = `pos_seerbit_va_${sessionId}`;
            bus_service.addChannel(channel);
        } else {
            // In case session becomes available slightly later
            const checkSessionInterval = setInterval(() => {
                const sid = getSessionId();
                if (sid) {
                    clearInterval(checkSessionInterval);
                    bus_service.addChannel(`pos_seerbit_va_${sid}`);
                }
            }, 1000);
        }

        const clean = (val) => String(val || "").trim();

        bus_service.subscribe("seerbit_va_notification", (payload) => {
            const { type, data } = payload || {};
            if (!data) return;

            const currentOrder = pos.getOrder ? pos.getOrder() : pos.get_order?.();

            if (type === "seerbit_va_payment_matched") {
                const isMatchingOrder = currentOrder && (
                    currentOrder.uuid === data.order_ref ||
                    currentOrder.pos_reference === data.order_ref ||
                    currentOrder.name === data.order_ref ||
                    currentOrder.id === data.order_id
                );

                if (isMatchingOrder) {
                    const paymentLines = currentOrder.get_paymentlines ? currentOrder.get_paymentlines() : (currentOrder.payment_ids || []);
                    const line = paymentLines.find(
                        (l) => l.payment_method_id?.payment_method_type === "transfer"
                    );
                    if (line) {
                        line.seerbit_transaction_ref = data.reference;
                        line.seerbit_payer_name = data.payer_name;
                        line.seerbit_va_transaction_id = data.transaction_id;
                        line.seerbit_is_matched = true;
                        // Populate standard Odoo fields so references show up on orders, payment forms, and reports
                        line.transaction_id = data.reference;
                        line.payment_ref_no = data.reference;
                        line.cardholder_name = data.payer_name || "Bank Transfer";
                        line.card_type = "Seerbit VA";
                        const refToDisplay = data.reference;
                        const payerToDisplay = data.payer_name || 'N/A';
                        if (refToDisplay && (!line.ticket || !line.ticket.includes(refToDisplay))) {
                            const receiptText = `\nBank Transfer Ref: ${refToDisplay}\nPayer: ${payerToDisplay}`;
                            if (line.setReceiptInfo) {
                                line.setReceiptInfo(receiptText);
                            } else {
                                line.ticket = (line.ticket || '') + receiptText;
                            }
                        }
                        if (line.setPaymentStatus) {
                            line.setPaymentStatus("done");
                        } else if (line.set_payment_status) {
                            line.set_payment_status("done");
                        }
                        notification.add(
                            _t("Payment of %s automatically matched from %s!", data.amount, data.payer_name),
                            { type: "success" }
                        );

                        // Trigger open modal if present so it transitions to success view and auto-validates on completion
                        const activeModal = line._seerbit_modal || pos._active_seerbit_modal;
                        if (activeModal && typeof activeModal.showSuccessAndClose === "function") {
                            activeModal.showSuccessAndClose(data);
                            return;
                        }

                        const isPaid = currentOrder.isPaid ? currentOrder.isPaid() : (currentOrder.is_paid ? currentOrder.is_paid() : false);
                        const due = currentOrder.get_due ? currentOrder.get_due() : (currentOrder.totalDue !== undefined ? currentOrder.totalDue : 0);
                        if ((isPaid || due <= 0.01) && due <= 0.01) {
                            setTimeout(async () => {
                                try {
                                    if (typeof pos.validateOrder === "function") {
                                        await pos.validateOrder();
                                    } else if (typeof pos._active_payment_screen?.validateOrder === "function") {
                                        await pos._active_payment_screen.validateOrder();
                                    }
                                } catch (e) {
                                    console.error("Error auto-validating order from bus listener:", e);
                                }
                            }, 100);
                        }
                    }
                }
            } else if (type === "seerbit_va_payment_received") {
                const msg = data.is_transferred ?
                    _t("Bank transfer of %s received (Transferred from %s, Ref: %s)", data.amount, data.from_till || "another till", data.reference) :
                    _t("New bank transfer received: %s from %s (Ref: %s)", data.amount, data.payer_name, data.reference);
                notification.add(msg, { type: "info" });

                // Transferred payments or payments with duplicate amounts MUST be manually matched
                if (data.is_transferred || data.has_multiple_same_amount || data.requires_manual_match) {
                    if (data.has_multiple_same_amount) {
                        notification.add(
                            _t("Multiple payments of %s received on this Virtual Account. Manual selection required.", data.amount),
                            { type: "warning" }
                        );
                    }
                    const activeModal = pos._active_seerbit_modal;
                    if (activeModal && typeof activeModal.fetchPayments === "function") {
                        activeModal.fetchPayments();
                    }
                    return;
                }

                if (currentOrder) {
                    const activeModal = pos._active_seerbit_modal;
                    // Ambiguity safety: check if modal payments list has multiple payments of this amount
                    if (activeModal?.state?.payments) {
                        const sameAmtPayments = activeModal.state.payments.filter(
                            (p) => p.matching_status === "unmatched" && Math.abs(parseFloat(p.amount) - parseFloat(data.amount || 0)) < 0.01
                        );
                        if (sameAmtPayments.length > 1 || (sameAmtPayments.length === 1 && sameAmtPayments[0].id !== data.transaction_id && sameAmtPayments[0].reference !== data.reference)) {
                            notification.add(
                                _t("Multiple payments of %s found on till. Manual selection required.", data.amount),
                                { type: "warning" }
                            );
                            if (typeof activeModal.fetchPayments === "function") {
                                activeModal.fetchPayments();
                            }
                            return;
                        }
                    }
                    const paymentLines = currentOrder.get_paymentlines ? currentOrder.get_paymentlines() : (currentOrder.payment_ids || []);
                    const line = paymentLines.find((l) => {
                        if (l.payment_method_id?.payment_method_type !== "transfer" || l.seerbit_is_matched) {
                            return false;
                        }
                        const isDynamic = l.payment_method_id?.seerbit_transfer_type === "dynamic";
                        if (isDynamic) {
                            return clean(l.seerbit_dynamic_account_number) === clean(data.account_number);
                        } else {
                            const lineVaNum = l.payment_method_id?.seerbit_va_account_number;
                            const lineAmt = l.getAmount ? l.getAmount() : (l.amount || 0);
                            return clean(lineVaNum) === clean(data.account_number) && Math.abs(parseFloat(lineAmt) - parseFloat(data.amount || 0)) < 0.01;
                        }
                    });

                    if (line) {
                        line.seerbit_transaction_ref = data.reference;
                        line.seerbit_payer_name = data.payer_name;
                        line.seerbit_va_transaction_id = data.transaction_id;
                        line.seerbit_is_matched = true;
                        line.transaction_id = data.reference;
                        line.payment_ref_no = data.reference;
                        line.cardholder_name = data.payer_name || "Bank Transfer";
                        line.card_type = "Seerbit VA";
                        const refToDisplay = data.reference;
                        const payerToDisplay = data.payer_name || 'N/A';
                        if (refToDisplay && (!line.ticket || !line.ticket.includes(refToDisplay))) {
                            const receiptText = `\nBank Transfer Ref: ${refToDisplay}\nPayer: ${payerToDisplay}`;
                            if (line.setReceiptInfo) {
                                line.setReceiptInfo(receiptText);
                            } else {
                                line.ticket = (line.ticket || '') + receiptText;
                            }
                        }
                        if (line.setPaymentStatus) {
                            line.setPaymentStatus("done");
                        } else if (line.set_payment_status) {
                            line.set_payment_status("done");
                        }
                        notification.add(
                            _t("Payment of %s automatically matched from %s!", data.amount, data.payer_name),
                            { type: "success" }
                        );

                        // Trigger open modal if present so it transitions to success view and auto-validates on completion
                        const activeModal = line._seerbit_modal || pos._active_seerbit_modal;
                        if (activeModal && typeof activeModal.showSuccessAndClose === "function") {
                            activeModal.showSuccessAndClose(data);
                            return;
                        }

                        const isPaid = currentOrder.isPaid ? currentOrder.isPaid() : (currentOrder.is_paid ? currentOrder.is_paid() : false);
                        const due = currentOrder.get_due ? currentOrder.get_due() : (currentOrder.totalDue !== undefined ? currentOrder.totalDue : 0);
                        if ((isPaid || due <= 0.01) && due <= 0.01) {
                            setTimeout(async () => {
                                try {
                                    if (typeof pos.validateOrder === "function") {
                                        await pos.validateOrder();
                                    } else if (typeof pos._active_payment_screen?.validateOrder === "function") {
                                        await pos._active_payment_screen.validateOrder();
                                    }
                                } catch (e) {
                                    console.error("Error auto-validating order from bus listener:", e);
                                }
                            }, 100);
                        }
                    }
                }
            }
        });
    },
};

registry.category("services").add("pos_seerbit_bus", posSeerbitBusService);


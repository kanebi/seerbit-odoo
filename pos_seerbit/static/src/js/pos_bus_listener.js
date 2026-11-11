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

                        // Trigger open modal if present so it transitions to success view and closes in 3s
                        const activeModal = line._seerbit_modal || pos._active_seerbit_modal;
                        if (activeModal && typeof activeModal.showSuccessAndClose === "function") {
                            activeModal.showSuccessAndClose(data);
                            return;
                        }

                        if (currentOrder.isPaid ? currentOrder.isPaid() : currentOrder.is_paid?.()) {
                            if (pos.validateOrder) {
                                pos.validateOrder();
                            }
                        }
                    }
                }
            } else if (type === "seerbit_va_payment_received") {
                notification.add(
                    _t("New bank transfer received: %s from %s (Ref: %s)", data.amount, data.payer_name, data.reference),
                    { type: "info" }
                );

                if (currentOrder) {
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

                        // Trigger open modal if present so it transitions to success view and closes in 3s
                        const activeModal = line._seerbit_modal || pos._active_seerbit_modal;
                        if (activeModal && typeof activeModal.showSuccessAndClose === "function") {
                            activeModal.showSuccessAndClose(data);
                            return;
                        }

                        if (currentOrder.isPaid ? currentOrder.isPaid() : currentOrder.is_paid?.()) {
                            if (pos.validateOrder) {
                                pos.validateOrder();
                            }
                        }
                    }
                }
            }
        });
    },
};

registry.category("services").add("pos_seerbit_bus", posSeerbitBusService);


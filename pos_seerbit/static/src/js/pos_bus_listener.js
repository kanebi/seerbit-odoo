/** @odoo-module **/

import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";

export const posSeerbitBusService = {
    dependencies: ["bus_service", "pos", "notification"],
    start(env, { bus_service, pos, notification }) {
        const sessionId = pos.config?.current_session_id?.[0] || pos.config?.current_session_id;
        if (!sessionId) return;

        const channel = `pos_seerbit_va_${sessionId}`;
        bus_service.addChannel(channel);

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
                        if (line.setReceiptInfo) {
                            line.setReceiptInfo(`\nBank Transfer Ref: ${data.reference}\nPayer: ${data.payer_name || 'N/A'}`);
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
                        if (currentOrder.isPaid ? currentOrder.isPaid() : currentOrder.is_paid?.()) {
                            if (pos.validateOrder) {
                                pos.validateOrder();
                            }
                        }
                    }
                }
            } else if (type === "seerbit_va_payment_received") {
                notification.add(
                    _t("New bank transfer received: %s from %s", data.amount, data.payer_name),
                    { type: "info" }
                );
            }
        });
    },
};

registry.category("services").add("pos_seerbit_bus", posSeerbitBusService);


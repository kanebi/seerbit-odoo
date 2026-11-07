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


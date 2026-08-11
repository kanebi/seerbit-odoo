/** @odoo-module **/

import { registry } from "@web/core/registry";

/**
 * Seerbit Payment Notification Service
 *
 * Listens for 'seerbit_payment_received' bus notifications (sent via the
 * 'broadcast' channel from webhook/cron) and shows a toast + soft-reloads
 * the current view so invoice status updates appear in real time.
 *
 * This follows the same pattern as Odoo 18's own account_notification_service,
 * calendar_notification_service, and simple_notification_service.
 */
export const seerbitNotificationService = {
    dependencies: ["bus_service", "notification", "action"],

    start(env, { bus_service, notification, action }) {
        bus_service.subscribe("seerbit_payment_received", (payload) => {
            notification.add(payload.message || "Seerbit payment received!", {
                type: "success",
                title: payload.title || "Seerbit",
                sticky: false,
            });

            // Delay the UI reload by 2.5 seconds to ensure the backend DB
            // transaction is fully committed before we fetch updated data.
            setTimeout(() => {
                action.doAction("soft_reload");
            }, 2500);
        });
        bus_service.start();
    },
};

registry.category("services").add("seerbitNotification", seerbitNotificationService);

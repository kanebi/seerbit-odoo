/** @odoo-module **/

import { registry } from "@web/core/registry";
import { user } from "@web/core/user";

const SEERBIT_NOTIFICATION_GROUP = "pos_seerbit.group_seerbit_notification_user";

/**
 * True when the current controller is a form (or equivalent) showing the
 * same record referenced in the bus payload.
 */
function isViewingRecord(action, payload) {
    const resModel = payload?.res_model;
    const resId = payload?.res_id;
    if (!resModel || !resId) {
        return false;
    }
    const controller = action.currentController;
    if (!controller) {
        return false;
    }
    const props = controller.props || {};
    const currentState = controller.currentState || {};
    const openModel = props.resModel || controller.action?.res_model;
    const openId = props.resId || currentState.resId;
    return openModel === resModel && Number(openId) === Number(resId);
}

/**
 * Seerbit Payment Notification Service
 *
 * Listens for 'seerbit_payment_received' bus notifications (sent via the
 * 'broadcast' channel from webhook/cron). Only members of
 * group_seerbit_notification_user see the toast; soft-reload runs only when
 * that user is currently viewing the related record.
 */
export const seerbitNotificationService = {
    dependencies: ["bus_service", "notification", "action"],

    start(env, { bus_service, notification, action }) {
        bus_service.subscribe("seerbit_payment_received", async (payload) => {
            if (!(await user.hasGroup(SEERBIT_NOTIFICATION_GROUP))) {
                return;
            }

            notification.add(payload.message || "Seerbit payment received!", {
                type: "success",
                title: payload.title || "Seerbit",
                sticky: false,
            });

            if (!isViewingRecord(action, payload)) {
                return;
            }

            // Delay so the backend transaction is committed before reload.
            setTimeout(() => {
                if (isViewingRecord(action, payload)) {
                    action.doAction("soft_reload");
                }
            }, 2500);
        });
        bus_service.start();
    },
};

registry.category("services").add("seerbitNotification", seerbitNotificationService);

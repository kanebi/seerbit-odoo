/** @odoo-module **/

import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";

export const posSeerbitBusService = {
    dependencies: ["bus_service", "pos", "notification", "orm"],
    start(env, { bus_service, pos, notification, orm }) {
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

        // Deduplication map to prevent duplicate bus messages across channels/reloads
        const processedEvents = new Map();
        const isDuplicateEvent = (eventKey, ttl = 120000) => {
            const now = Date.now();
            for (const [key, timestamp] of processedEvents.entries()) {
                if (now - timestamp > ttl) {
                    processedEvents.delete(key);
                }
            }
            if (processedEvents.has(eventKey)) {
                return true;
            }
            processedEvents.set(eventKey, now);
            return false;
        };

        const getAllOrders = () => {
            let orders = [];
            if (pos.models?.["pos.order"]?.getAll) {
                orders = [...pos.models["pos.order"].getAll()];
            } else if (pos.get_open_orders) {
                orders = [...pos.get_open_orders()];
            } else if (pos.get_order_list) {
                orders = [...pos.get_order_list()];
            }
            const cur = pos.getOrder ? pos.getOrder() : pos.get_order?.();
            if (cur && !orders.some(o => (o.uuid && o.uuid === cur.uuid) || o === cur)) {
                orders.unshift(cur);
            }
            // Prioritize currently active order if known
            if (cur) {
                orders.sort((a, b) => ((a === cur || a.uuid === cur.uuid) ? -1 : (b === cur || b.uuid === cur.uuid) ? 1 : 0));
            }
            return orders;
        };

        const autoValidateOrder = (order) => {
            if (!order || order.finalized || order.state === "paid") {
                return;
            }
            const isPaid = order.isPaid ? order.isPaid() : (order.is_paid ? order.is_paid() : false);
            const due = order.get_due ? order.get_due() : (order.totalDue !== undefined ? order.totalDue : 0);
            if (!isPaid && due > 0.01) {
                return;
            }

            setTimeout(async () => {
                try {
                    const cur = pos.getOrder ? pos.getOrder() : pos.get_order?.();
                    const isCurrent = cur && ((order.uuid && order.uuid === cur.uuid) || order === cur);
                    if (isCurrent && typeof pos.validateOrder === "function") {
                        await pos.validateOrder();
                    } else if (isCurrent && typeof pos._active_payment_screen?.validateOrder === "function") {
                        await pos._active_payment_screen.validateOrder();
                    } else {
                        // Background order or when cashier is on TicketScreen
                        if (typeof luxon !== "undefined" && luxon.DateTime) {
                            order.date_order = luxon.DateTime.now().toFormat("yyyy-MM-dd HH:mm:ss");
                        } else {
                            order.date_order = new Date().toISOString();
                        }
                        if (typeof pos.addPendingOrder === "function") {
                            pos.addPendingOrder([order.id]);
                        }
                        order.state = "paid";
                        if (typeof order.set_screen_data === "function") {
                            order.set_screen_data({ name: "ReceiptScreen" });
                        }
                        if (typeof pos.syncAllOrders === "function") {
                            await pos.syncAllOrders({ throw: false });
                        }
                    }
                } catch (e) {
                    console.error("Error auto-validating order from bus listener:", e);
                }
            }, 100);
        };

        const matchLineWithData = (line, order, data) => {
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

            // Sync match atomically to backend so subsequent transactions of same amount don't collision-block
            if (data.transaction_id && orm?.call) {
                const orderUuid = order.uuid || order.pos_reference || order.name || String(order.id);
                orm.call("seerbit.va.transaction", "match_payment_to_pos_order_atomic", [
                    data.transaction_id,
                    orderUuid,
                    pos.user?.id || false,
                    "auto_matched"
                ]).catch(err => console.warn("Could not mark transaction as matched in backend:", err));
            }

            // Trigger open modal if present so it transitions to success view and auto-validates on completion
            const activeModal = line._seerbit_modal || (pos._active_seerbit_modal?.paymentLine === line ? pos._active_seerbit_modal : null);
            if (activeModal && typeof activeModal.showSuccessAndClose === "function") {
                activeModal.showSuccessAndClose(data);
                return;
            }

            autoValidateOrder(order);
        };

        bus_service.subscribe("seerbit_va_notification", (payload) => {
            const { type, data } = payload || {};
            if (!data) return;

            // Deduplicate incoming events across channels, reloads, and webhooks
            const eventRef = data.reference || data.transaction_id || "";
            const eventKey = `${type}_${eventRef}_${data.amount || ""}_${data.matching_status || ""}`;
            if (eventRef && isDuplicateEvent(eventKey)) {
                console.log("SeerBit POS: Suppressing duplicate bus event:", eventKey);
                return;
            }

            const allOrders = getAllOrders();

            if (type === "seerbit_va_payment_matched") {
                const targetOrder = allOrders.find(
                    (o) => o && (
                        o.uuid === data.order_ref ||
                        o.pos_reference === data.order_ref ||
                        o.name === data.order_ref ||
                        o.id === data.order_id
                    )
                );

                if (targetOrder) {
                    const paymentLines = targetOrder.get_paymentlines ? targetOrder.get_paymentlines() : (targetOrder.payment_ids || []);
                    const line = paymentLines.find(
                        (l) => l.payment_method_id?.payment_method_type === "transfer" && !l.seerbit_is_matched
                    ) || paymentLines.find((l) => l.payment_method_id?.payment_method_type === "transfer");

                    if (line) {
                        matchLineWithData(line, targetOrder, data);
                        notification.add(
                            _t("Payment of %s automatically matched from %s!", data.amount, data.payer_name),
                            { type: "success", sticky: false }
                        );
                    }
                }
            } else if (type === "seerbit_va_payment_received") {
                if (data.is_transferred) {
                    notification.add(
                        _t("Bank transfer of %s received (Transferred from %s, Ref: %s)", data.amount, data.from_till || "another till", data.reference),
                        { type: "info", sticky: false }
                    );
                    const activeModal = pos._active_seerbit_modal;
                    if (activeModal && typeof activeModal.fetchPayments === "function") {
                        activeModal.fetchPayments();
                    }
                    return;
                }

                // 1. Dynamic Transfer Check across all open orders (uniquely identified by dynamic account or reference)
                let matchedDynamic = false;
                for (const ord of allOrders) {
                    if (ord.finalized || ord.state === "paid" || ord.state === "done") continue;
                    const paymentLines = ord.get_paymentlines ? ord.get_paymentlines() : (ord.payment_ids || []);
                    const line = paymentLines.find((l) => {
                        if (l.payment_method_id?.payment_method_type !== "transfer" || l.seerbit_is_matched) {
                            return false;
                        }
                        if (l.payment_method_id?.seerbit_transfer_type !== "dynamic") {
                            return false;
                        }
                        const accMatch = clean(l.seerbit_dynamic_account_number) && clean(l.seerbit_dynamic_account_number) === clean(data.account_number);
                        const refMatch = (clean(l.seerbit_dynamic_reference) && clean(data.reference).includes(clean(l.seerbit_dynamic_reference))) ||
                                         (clean(data.reference) && (
                                             clean(data.reference).includes(clean(ord.pos_reference || "")) ||
                                             clean(data.reference).includes(clean(ord.name || ""))
                                         ));
                        return accMatch || refMatch;
                    });
                    if (line) {
                        matchLineWithData(line, ord, data);
                        notification.add(
                            _t("Payment of %s automatically matched from %s!", data.amount, data.payer_name),
                            { type: "success", sticky: false }
                        );
                        matchedDynamic = true;
                        break;
                    }
                }
                if (matchedDynamic) {
                    return;
                }

                // 2. Static Transfer Check across all open orders
                const candidateMatches = [];
                for (const ord of allOrders) {
                    if (ord.finalized || ord.state === "paid" || ord.state === "done") continue;
                    const paymentLines = ord.get_paymentlines ? ord.get_paymentlines() : (ord.payment_ids || []);
                    for (const l of paymentLines) {
                        if (l.payment_method_id?.payment_method_type !== "transfer" || l.seerbit_is_matched) continue;
                        if (l.payment_method_id?.seerbit_transfer_type === "dynamic") continue;

                        const lineVaNum = l.payment_method_id?.seerbit_va_account_number;
                        const lineAmt = l.getAmount ? l.getAmount() : (l.amount || 0);
                        if (clean(lineVaNum) === clean(data.account_number) && Math.abs(parseFloat(lineAmt) - parseFloat(data.amount || 0)) < 0.01) {
                            candidateMatches.push({ order: ord, line: l });
                        }
                    }
                }

                if (candidateMatches.length > 0) {
                    // Pick candidate (prioritizes current order if present, or first awaiting order)
                    const { order: targetOrder, line: targetLine } = candidateMatches[0];
                    matchLineWithData(targetLine, targetOrder, data);
                    notification.add(
                        _t("Payment of %s automatically matched from %s!", data.amount, data.payer_name),
                        { type: "success", sticky: false }
                    );
                    return;
                }

                // 3. Unmatched payment: If duplicate amounts on till with no clear order, alert cashier and refresh modal
                if (data.has_multiple_same_amount || data.requires_manual_match) {
                    notification.add(
                        _t("Multiple payments of %s received on this Virtual Account. Manual selection required.", data.amount),
                        { type: "warning", sticky: false }
                    );
                } else {
                    notification.add(
                        _t("New bank transfer received: %s from %s (Ref: %s)", data.amount, data.payer_name, data.reference),
                        { type: "info", sticky: false }
                    );
                }
                const activeModal = pos._active_seerbit_modal;
                if (activeModal && typeof activeModal.fetchPayments === "function") {
                    activeModal.fetchPayments();
                }
            }
        });
    },
};

registry.category("services").add("pos_seerbit_bus", posSeerbitBusService);

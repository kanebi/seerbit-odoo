/** @odoo-module **/

import { PaymentInterface } from "@point_of_sale/app/utils/payment/payment_interface";
import { _t } from "@web/core/l10n/translation";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import FirebaseInit from "./firebase_init";
import FirebaseListener from "./firebase_listener";

function initializeSeerbitFirebase(orm) {
    FirebaseInit.initializeFirebase(orm)
        .then((success) => {
            if (!success) {
                console.warn(
                    "Firestore initialization failed for Seerbit payments. Status:",
                    FirebaseInit.getFirebaseStatus()
                );
            }
        })
        .catch((error) => {
            console.error("Firestore initialization error:", error);
        });
}

export default class SeerbitPayment extends PaymentInterface {
    setup() {
        super.setup(...arguments);
        this.seerbit_polling = null;
        this.seerbit_was_cancelled = false;
        this.supports_reversals = false;
        initializeSeerbitFirebase(this.env.services.orm);
    }

    async sendPaymentRequest(uuid) {
        super.sendPaymentRequest(uuid);
        const order = this.pos.getOrder();
        const paymentLine = order.getSelectedPaymentline();
        if (paymentLine.getAmount() < 0.01) {
            await this.env.services.dialog.add(AlertDialog, {
                title: _t("Amount Error"),
                body: _t("Cannot process transactions with invalid amount."),
            });
            return false;
        }
        paymentLine.setPaymentStatus("waitingSeerbit");
        return this._send_seerbit_payment_request_to_firestore(paymentLine);
    }

    async sendPaymentCancel(order, uuid) {
        super.sendPaymentCancel(order, uuid);
        this.seerbit_was_cancelled = true;
        this._reset_seerbit_state();
        return true;
    }

    _seerbit_pay_data(paymentLine) {
        const order = this.pos.getOrder();
        const paymentMethod = this.payment_method_id;
        const now = new Date();
        const day = String(now.getDate()).padStart(2, "0");
        const month = String(now.getMonth() + 1).padStart(2, "0");
        const year = now.getFullYear();
        const hour = String(now.getHours()).padStart(2, "0");
        const minute = String(now.getMinutes()).padStart(2, "0");
        const receivedDateTime = `${day}/${month}/${year} ${hour}:${minute}`;
        const metadata = JSON.stringify({
            created_by: "odoo_pos_seerbit",
            created_time: now.toISOString(),
            order_id: order?.uuid || order?.id,
            pos_config_id: this.pos.config?.id,
            user_id: this.pos.user?.id,
        });
        return {
            id: order?.uuid || order?.id,
            posid: paymentMethod?.seerbit_terminal_id || "",
            merchantid: "",
            metadata: metadata,
            transactionValue: paymentLine.getAmount()?.toFixed(2),
            status: "open",
            transactionTime: "",
            sessionId: "",
            receivedDateTime: receivedDateTime,
            transactionRef: "",
            pubkey: paymentMethod?.seerbit_public_key || "",
        };
    }

    _send_seerbit_payment_request_to_firestore(paymentLine) {
        let payload;
        try {
            payload = this._seerbit_pay_data(paymentLine);
        } catch (error) {
            console.error("Error creating payment payload:", error);
            return Promise.reject(error);
        }
        if (!paymentLine.payment_method_id?.id) {
            return false;
        }

        return this.env.services.orm
            .call(
                "pos.payment.method",
                "send_seerbit_payment_request",
                [[paymentLine.payment_method_id.id], payload],
                {}
            )
            .then(() => {
                localStorage.setItem("pending_transaction", JSON.stringify(payload));
                FirebaseListener.listenForReconciliation(payload.id, this.env);
                return this._seerbit_start_get_status_polling(paymentLine);
            })
            .catch(async (error) => {
                console.error("Payment request failed:", error);
                if (paymentLine?.setPaymentStatus) {
                    paymentLine.setPaymentStatus("waitingSeerbit");
                }
                await this.env.services.dialog.add(AlertDialog, {
                    title: _t("Seerbit Warning"),
                    body: _t("Could not send payment request. You can force confirm if payment was made."),
                });
                return false;
            });
    }

    _seerbit_start_get_status_polling(paymentLine) {
        const self = this;
        return new Promise((resolve, reject) => {
            clearInterval(self.seerbit_polling);
            self._seerbit_poll_for_response(paymentLine, resolve, reject);
            self.seerbit_polling = setInterval(() => {
                self._seerbit_poll_for_response(paymentLine, resolve, reject);
            }, 3500);
        }).finally(() => {
            self._reset_seerbit_state();
        });
    }

    async _seerbit_poll_for_response(paymentLine, resolve, reject) {
        if (this.seerbit_was_cancelled) {
            paymentLine.setPaymentStatus("retry");
            this._reset_seerbit_state();
            return reject();
        }

        const order = this.pos.getOrder();
        if (!order || !order.getSelectedPaymentline()) {
            return;
        }

        try {
            const completedTransaction = localStorage.getItem("completed_transaction");
            if (!completedTransaction) {
                return;
            }

            const transactionData = JSON.parse(completedTransaction);
            const pendingTransaction = JSON.parse(localStorage.getItem("pending_transaction") || "{}");
            if (pendingTransaction && pendingTransaction.id !== transactionData.id) {
                console.warn("Transaction ID mismatch, ignoring stale transaction");
                localStorage.removeItem("completed_transaction");
                return;
            }

            paymentLine.setPaymentStatus("done");
            const transactionId =
                transactionData?.sessionId || transactionData?.transactionRef || transactionData.id;
            paymentLine.setReceiptInfo("Transaction ID: " + transactionId);
            paymentLine.transaction_id = transactionId;
            paymentLine.card_type = "Seerbit";
            paymentLine.cardholder_name = "Seerbit Payment";

            this.env.services.dialog.add(AlertDialog, {
                title: _t("Payment Successful"),
                body: _t("Payment has been successfully processed."),
            });

            resolve(true);
        } catch (error) {
            console.error("Error processing payment response:", error);
            this._reset_seerbit_state();

            if (paymentLine) {
                paymentLine.setPaymentStatus("errorSeerbit");
            }

            this.env.services.dialog.add(AlertDialog, {
                title: _t("Payment Error"),
                body: _t("An error occurred while processing your payment. Please try again."),
            });

            reject();
        }
    }

    _reset_seerbit_state() {
        clearInterval(this.seerbit_polling);
        this.seerbit_polling = null;
        this.seerbit_was_cancelled = false;
        localStorage.removeItem("pending_transaction");
        localStorage.removeItem("completed_transaction");
        if (this.pos) {
            this.pos.paymentTerminalInProgress = false;
        }
    }

    async sendForceDone(line) {
        if (line && line.payment_method_id && line.payment_method_id.use_payment_terminal === "seerbit") {
            line.setPaymentStatus("done");
            line.setReceiptInfo(
                "Transaction ID: " + (line.pos_order_id?.uuid || "").toString()
            );
            this._reset_seerbit_state();
            await this.env.services.dialog.add(AlertDialog, {
                title: "Seerbit Payment",
                body: "Payment forcibly confirmed as done.",
            });
            this.pos.paymentTerminalInProgress = false;
        }
    }

    close() {
        this.seerbit_was_cancelled = true;
        this._reset_seerbit_state();
    }
}

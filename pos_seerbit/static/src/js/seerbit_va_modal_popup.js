/** @odoo-module **/

import { Component, useState, onMounted, onWillUnmount } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { AlertDialog, ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";

export class SeerbitVaModalPopup extends Component {
    static template = "pos_seerbit.SeerbitVaModalPopup";
    static components = { Dialog };
    static props = {
        paymentLine: { type: Object },
        pos: { type: Object, optional: true },
        validateOrder: { type: Function, optional: true },
        close: { type: Function, optional: true },
    };

    setup() {
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        this.orm = useService("orm");
        this.busService = useService("bus_service");
        this.state = useState({
            copied: false,
            generatingDynamicVa: false,
            error: null,
            remainingSeconds: 0,
            isExpired: false,
            dynamicVa: null,
            unmatchedCount: 0,
            showPaymentsList: false,
            payments: [],
            loadingPayments: false,
            syncingPayments: false,
            paymentsSearchQuery: "",
            isSuccess: false,
            successData: null,
            successCountdown: 10,
        });

        this.countdownInterval = null;
        this.successTimer = null;
        this.statusPoller = null;
        this._busHandler = null;

        onMounted(() => {
            if (this.paymentLine) {
                this.paymentLine._seerbit_modal_open = true;
                this.paymentLine._seerbit_modal = this;
            }
            if (this.pos) {
                this.pos._active_seerbit_modal = this;
            }

            // Periodic safety check: if paymentLine is matched by bus service or anything else,
            // immediately trigger the 3-second success countdown and finish!
            this.statusPoller = setInterval(() => {
                if (this.paymentLine?.seerbit_is_matched && !this.state.isSuccess) {
                    this.showSuccessAndClose({
                        reference: this.paymentLine.seerbit_transaction_ref,
                        payer_name: this.paymentLine.seerbit_payer_name,
                        amount: this.expectedAmount,
                        transaction_id: this.paymentLine.seerbit_va_transaction_id,
                    });
                }
            }, 500);

            if (this.isDynamic) {
                if (this.paymentLine?.seerbit_dynamic_account_number) {
                    this.state.dynamicVa = {
                        account_number: this.paymentLine.seerbit_dynamic_account_number,
                        bank_name: this.paymentLine.seerbit_dynamic_bank_name,
                        account_name: this.paymentLine.seerbit_dynamic_account_name,
                        va_id: this.paymentLine.seerbit_dynamic_va_id,
                        reference: this.paymentLine.seerbit_dynamic_reference,
                        expires_at: this.paymentLine.seerbit_dynamic_expires_at,
                    };
                    this._startCountdownTimer();
                }
            } else {
                this._checkPendingPaymentsCount();
                this.fetchPayments();
            }

            // Real-time bus listener for incoming payments
            if (this.busService) {
                this._busHandler = (payload) => {
                    const { type, data } = payload || {};
                    if (!data || this.state.isSuccess) return;

                    const cleanAcc = (acc) => String(acc || "").trim();
                    const isForThisAccount = !data.account_number || cleanAcc(data.account_number) === cleanAcc(this.accountNumber);
                    const lineAmt = this.expectedAmount;
                    const isExactAmt = Math.abs(parseFloat(lineAmt) - parseFloat(data.amount || 0)) < 0.01;

                    if (type === "seerbit_va_payment_matched" || (type === "seerbit_va_payment_received" && (this.isDynamic ? isForThisAccount : (isForThisAccount && isExactAmt)))) {
                        this.showSuccessAndClose(data);
                    } else if (isForThisAccount) {
                        this.fetchPayments();
                        this._checkPendingPaymentsCount();
                    }
                };
                this.busService.subscribe("seerbit_va_notification", this._busHandler);
            }
        });

        onWillUnmount(() => {
            this._stopCountdownTimer();
            if (this.statusPoller) {
                clearInterval(this.statusPoller);
                this.statusPoller = null;
            }
            if (this.successTimer) {
                clearInterval(this.successTimer);
                this.successTimer = null;
            }
            if (this.paymentLine) {
                this.paymentLine._seerbit_modal_open = false;
                if (this.paymentLine._seerbit_modal === this) {
                    delete this.paymentLine._seerbit_modal;
                }
            }
            if (this.pos && this.pos._active_seerbit_modal === this) {
                this.pos._active_seerbit_modal = null;
            }
            if (this.busService && this._busHandler) {
                this.busService.unsubscribe("seerbit_va_notification", this._busHandler);
            }
        });
    }

    get paymentLine() {
        return this.props.paymentLine;
    }

    get pos() {
        return this.props.pos || this.env.services.pos;
    }

    get paymentMethod() {
        return this.paymentLine?.payment_method_id;
    }

    get isDynamic() {
        return this.paymentMethod?.seerbit_transfer_type === "dynamic";
    }

    get hasGeneratedDynamicVa() {
        if (!this.isDynamic) {
            return true;
        }
        return Boolean(this.state.dynamicVa?.account_number || this.paymentLine?.seerbit_dynamic_account_number);
    }

    get vaId() {
        if (this.isDynamic) {
            return this.state.dynamicVa?.va_id || this.paymentLine?.seerbit_dynamic_va_id;
        }
        const va = this.paymentMethod?.seerbit_va_id;
        return Array.isArray(va) ? va[0] : (typeof va === "object" && va !== null ? va.id : va);
    }

    get accountNumber() {
        if (this.isDynamic) {
            return this.state.dynamicVa?.account_number || this.paymentLine?.seerbit_dynamic_account_number || (this.state.generatingDynamicVa ? "Generating..." : "");
        }
        return this.paymentMethod?.seerbit_va_account_number || "Not Configured";
    }

    get bankName() {
        if (this.isDynamic) {
            return this.state.dynamicVa?.bank_name || this.paymentLine?.seerbit_dynamic_bank_name || "SeerBit Virtual Bank";
        }
        return this.paymentMethod?.seerbit_va_bank_name || "SeerBit Virtual Bank";
    }

    get accountName() {
        if (this.isDynamic) {
            return this.state.dynamicVa?.account_name || this.paymentLine?.seerbit_dynamic_account_name || this.pos?.config?.name || "Store Account";
        }
        return this.paymentMethod?.seerbit_va_name || this.pos?.config?.name || "Store Account";
    }

    get expectedAmount() {
        return this.paymentLine ? (this.paymentLine.getAmount ? this.paymentLine.getAmount() : this.paymentLine.amount || 0) : 0;
    }

    get expectedAmountFormatted() {
        return this.env.utils.formatCurrency(this.expectedAmount);
    }

    get filteredPayments() {
        const query = this.state.paymentsSearchQuery.toLowerCase().trim();
        if (!query) return this.state.payments;
        return this.state.payments.filter((p) =>
            (p.payer_name && p.payer_name.toLowerCase().includes(query)) ||
            (p.reference && p.reference.toLowerCase().includes(query)) ||
            (String(p.amount).includes(query))
        );
    }

    formatAmount(amount) {
        return this.env.utils.formatCurrency(amount || 0);
    }

    formatDate(dateStr) {
        if (!dateStr) return "";
        try {
            const d = new Date(dateStr);
            return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        } catch {
            return dateStr;
        }
    }

    isExactAmount(amount) {
        return Math.abs(parseFloat(amount) - parseFloat(this.expectedAmount)) < 0.01;
    }

    _startCountdownTimer() {
        this._stopCountdownTimer();
        const expiresAt = this.state.dynamicVa?.expires_at || this.paymentLine?.seerbit_dynamic_expires_at;
        if (!expiresAt) return;

        const updateTimer = () => {
            const now = Date.now();
            const diffMs = expiresAt - now;
            const diffSec = Math.max(0, Math.floor(diffMs / 1000));
            this.state.remainingSeconds = diffSec;
            this.state.isExpired = diffSec <= 0;

            if (diffSec <= 0) {
                this._stopCountdownTimer();
            }
        };

        updateTimer();
        this.countdownInterval = setInterval(updateTimer, 1000);
    }

    _stopCountdownTimer() {
        if (this.countdownInterval) {
            clearInterval(this.countdownInterval);
            this.countdownInterval = null;
        }
    }

    get formattedCountdown() {
        const totalSec = this.state.remainingSeconds;
        const mins = Math.floor(totalSec / 60);
        const secs = totalSec % 60;
        return `${mins.toString().padStart(2, "0")}:${secs.toString().padStart(2, "0")}`;
    }

    async generateDynamicVa() {
        const amount = this.expectedAmount;
        if (amount <= 1.0) {
            this.dialog.add(AlertDialog, {
                title: _t("Amount Too Low"),
                body: _t("Transfer payment amount must be greater than 1.00. Please enter a valid amount before generating bank account."),
            });
            return;
        }
        if (this.state.isExpired && this.paymentLine) {
            this.paymentLine.seerbit_dynamic_account_number = null;
            this.paymentLine.seerbit_dynamic_bank_name = null;
            this.paymentLine.seerbit_dynamic_account_name = null;
            this.paymentLine.seerbit_dynamic_va_id = null;
            this.paymentLine.seerbit_dynamic_reference = null;
            this.paymentLine.seerbit_dynamic_expires_at = null;
            this.state.isExpired = false;
        }
        await this._initDynamicVa();
    }

    resetDynamicVa() {
        this._stopCountdownTimer();
        this.state.dynamicVa = null;
        this.state.error = null;
        this.state.remainingSeconds = 0;
        this.state.isExpired = false;
        this.state.showPaymentsList = false;
        if (this.paymentLine) {
            this.paymentLine.seerbit_dynamic_account_number = null;
            this.paymentLine.seerbit_dynamic_bank_name = null;
            this.paymentLine.seerbit_dynamic_account_name = null;
            this.paymentLine.seerbit_dynamic_va_id = null;
            this.paymentLine.seerbit_dynamic_reference = null;
            this.paymentLine.seerbit_dynamic_expires_at = null;
        }
    }

    async _initDynamicVa() {
        if (this.paymentLine?.seerbit_dynamic_account_number && !this.state.isExpired) {
            this.state.dynamicVa = {
                account_number: this.paymentLine.seerbit_dynamic_account_number,
                bank_name: this.paymentLine.seerbit_dynamic_bank_name,
                account_name: this.paymentLine.seerbit_dynamic_account_name,
                va_id: this.paymentLine.seerbit_dynamic_va_id,
                reference: this.paymentLine.seerbit_dynamic_reference,
                expires_at: this.paymentLine.seerbit_dynamic_expires_at,
            };
            this._startCountdownTimer();
            return;
        }

        this.state.generatingDynamicVa = true;
        this.state.error = null;
        try {
            const order = this.pos?.getOrder ? this.pos.getOrder() : this.pos?.get_order?.();
            const orderRef = (order?.pos_reference || order?.name || `ORD_${Date.now()}`).replace(/[^a-zA-Z0-9_-]/g, "");
            const amount = this.expectedAmount;
            const customer = order?.get_partner ? order.get_partner() : order?.partner;

            const res = await this.orm.call(
                "pos.payment.method",
                "create_dynamic_va_for_pos",
                [[this.paymentMethod.id], orderRef, amount, customer?.name || null, customer?.email || null]
            );

            const expiryMinutes = res.expiry_minutes || 30;
            const expiresAt = Date.now() + expiryMinutes * 60 * 1000;
            res.expires_at = expiresAt;

            this.state.dynamicVa = res;
            if (this.paymentLine) {
                this.paymentLine.seerbit_dynamic_account_number = res.account_number;
                this.paymentLine.seerbit_dynamic_bank_name = res.bank_name;
                this.paymentLine.seerbit_dynamic_account_name = res.account_name;
                this.paymentLine.seerbit_dynamic_va_id = res.va_id;
                this.paymentLine.seerbit_dynamic_reference = res.reference;
                this.paymentLine.seerbit_dynamic_expires_at = expiresAt;
            }
            this._startCountdownTimer();
        } catch (err) {
            console.error("Failed to generate dynamic virtual account:", err);
            this.state.error = err.message || _t("Failed to generate dynamic Virtual Account.");
            this.notification.add(this.state.error, { type: "danger" });
        } finally {
            this.state.generatingDynamicVa = false;
        }
    }

    async togglePaymentsList() {
        this.state.showPaymentsList = !this.state.showPaymentsList;
        if (this.state.showPaymentsList && !this.state.payments.length) {
            await this.fetchPayments();
        }
    }

    async openIncomingPaymentsModal() {
        await this.togglePaymentsList();
    }

    async fetchPayments() {
        if (!this.vaId || this.state.isSuccess) return;
        this.state.loadingPayments = true;
        try {
            const order = this.pos?.getOrder ? this.pos.getOrder() : this.pos?.get_order?.();
            const orderCreatedAt = order?.date_order || order?.creation_date?.toISOString?.() || new Date().toISOString();
            const data = await this.orm.call(
                "seerbit.va.transaction",
                "get_unmatched_payments_for_pos",
                [this.vaId, orderCreatedAt, 30]
            );
            if (!this.state.isSuccess) {
                this.state.payments = data || [];
                this.state.unmatchedCount = this.state.payments.length;
            }
        } catch (err) {
            const errMsg = String(err?.message || err || "");
            if (!this.state.isSuccess && !errMsg.toLowerCase().includes("destroyed")) {
                console.error("Failed to load incoming payments", err);
                this.notification.add(_t("Could not retrieve incoming payments: ") + errMsg, { type: "danger" });
            }
        } finally {
            this.state.loadingPayments = false;
        }
    }

    async syncLiveFromSeerbit() {
        if (!this.vaId) return;
        this.state.syncingPayments = true;
        try {
            await this.orm.call(
                "seerbit.virtual.account",
                "action_refresh_balance",
                [[this.vaId]]
            );
            await this.fetchPayments();
            this.notification.add(_t("Payments refreshed!"), { type: "success" });
        } catch (err) {
            this.notification.add(_t("Could not refresh: ") + (err.message || err), { type: "warning" });
        } finally {
            this.state.syncingPayments = false;
        }
    }

    async selectPayment(payment) {
        const amountFormatted = this.formatAmount(payment.amount);
        this.dialog.add(ConfirmationDialog, {
            title: _t("Confirm Payment"),
            body: _t(
                `Confirm payment of ${amountFormatted} from "${payment.payer_name}"?`
            ),
            confirm: async () => {
                await this._executePaymentSelection(payment);
            },
            cancel: () => { },
        });
    }

    async _executePaymentSelection(payment) {
        try {
            const order = this.pos?.getOrder ? this.pos.getOrder() : this.pos?.get_order?.();
            const orderUuid = order?.uuid || order?.pos_reference || order?.name || String(order?.id);
            const res = await this.orm.call(
                "seerbit.va.transaction",
                "match_payment_to_pos_order_atomic",
                [payment.id, orderUuid]
            );
            if (res.success) {
                this._onPaymentConfirmed(res);
            } else {
                this.notification.add(res.error || _t("Payment selection failed."), { type: "danger" });
                await this.fetchPayments();
            }
        } catch (err) {
            this.notification.add(_t("Error matching payment: ") + (err.message || err), { type: "danger" });
        }
    }

    _onPaymentConfirmed(transaction) {
        this.showSuccessAndClose(transaction);
    }

    showSuccessAndClose(data) {
        if (this.state.isSuccess) return;
        this._stopCountdownTimer();
        if (this.statusPoller) {
            clearInterval(this.statusPoller);
            this.statusPoller = null;
        }
        this.state.isSuccess = true;
        this.state.successData = data || {};
        this.state.successCountdown = 10;

        const line = this.paymentLine;
        if (line) {
            line._seerbit_modal_open = true;
            if (data.reference) line.seerbit_transaction_ref = data.reference;
            if (data.payer_name) line.seerbit_payer_name = data.payer_name;
            if (data.transaction_id || data.id) line.seerbit_va_transaction_id = data.transaction_id || data.id;
            line.seerbit_is_matched = true;
            if (data.reference) {
                line.transaction_id = data.reference;
                line.payment_ref_no = data.reference;
            }
            line.cardholder_name = data.payer_name || line.cardholder_name || "Bank Transfer";
            line.card_type = "Seerbit VA";
            const refToDisplay = data.reference || line.seerbit_transaction_ref;
            const payerToDisplay = data.payer_name || line.seerbit_payer_name || 'N/A';
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
        }

        const amountFormatted = this.formatAmount(data?.amount || this.expectedAmount);
        const payerName = data?.payer_name || line?.seerbit_payer_name || "Bank Customer";
        this.notification.add(
            _t("Payment of %s confirmed from %s!", amountFormatted, payerName),
            { type: "success" }
        );

        if (this.successTimer) {
            clearInterval(this.successTimer);
        }
        this.successTimer = setInterval(() => {
            if (this.state.successCountdown > 1) {
                this.state.successCountdown -= 1;
            } else {
                this._finishSuccess();
            }
        }, 1000);
    }

    _finishSuccess() {
        if (this.statusPoller) {
            clearInterval(this.statusPoller);
            this.statusPoller = null;
        }
        if (this.successTimer) {
            clearInterval(this.successTimer);
            this.successTimer = null;
        }
        const line = this.paymentLine;
        if (line) {
            line._seerbit_modal_open = false;
            delete line._seerbit_modal;
        }
        if (this.pos && this.pos._active_seerbit_modal === this) {
            this.pos._active_seerbit_modal = null;
        }

        try {
            if (this.props.close) {
                this.props.close();
            } else if (this.env.dialogData?.close) {
                this.env.dialogData.close();
            }
        } catch (e) {
            console.error("Error closing modal after success:", e);
        }

        const order = this.pos?.getOrder ? this.pos.getOrder() : this.pos?.get_order?.();
        if (order && (order.isPaid ? order.isPaid() : order.is_paid?.())) {
            try {
                if (this.props.validateOrder) {
                    this.props.validateOrder();
                } else if (this.pos?.validateOrder) {
                    this.pos.validateOrder();
                }
            } catch (e) {
                console.error("Error validating order:", e);
            }
        }
    }

    _closeModal() {
        this._stopCountdownTimer();
        if (this.statusPoller) {
            clearInterval(this.statusPoller);
            this.statusPoller = null;
        }
        if (this.successTimer) {
            clearInterval(this.successTimer);
            this.successTimer = null;
        }
        const line = this.paymentLine;
        if (line) {
            line._seerbit_modal_open = false;
            delete line._seerbit_modal;
        }
        if (this.pos && this.pos._active_seerbit_modal === this) {
            this.pos._active_seerbit_modal = null;
        }
        try {
            if (this.props.close) {
                this.props.close();
            } else if (this.env.dialogData?.close) {
                this.env.dialogData.close();
            }
        } catch (e) {
            console.error("Error closing modal:", e);
        }
    }

    async _checkPendingPaymentsCount() {
        if (!this.vaId) return;
        try {
            const count = await this.orm.call(
                "seerbit.va.transaction",
                "search_count",
                [[
                    ["va_id", "=", this.vaId],
                    ["matching_status", "=", "unmatched"],
                    ["status", "=", "successful"]
                ]]
            );
            this.state.unmatchedCount = count;
        } catch (e) {
            console.debug("Failed to fetch pending VA payment count", e);
        }
    }
}

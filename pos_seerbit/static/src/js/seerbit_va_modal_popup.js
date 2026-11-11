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
        });

        this.countdownInterval = null;

        onMounted(() => {
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
            }
        });

        onWillUnmount(() => {
            this._stopCountdownTimer();
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
        return this.paymentMethod?.seerbit_va_id;
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
        if (!this.vaId) return;
        this.state.loadingPayments = true;
        try {
            const order = this.pos?.getOrder ? this.pos.getOrder() : this.pos?.get_order?.();
            const orderCreatedAt = order?.date_order || order?.creation_date?.toISOString?.() || new Date().toISOString();
            const data = await this.orm.call(
                "seerbit.va.transaction",
                "get_unmatched_payments_for_pos",
                [this.vaId, orderCreatedAt, 30]
            );
            this.state.payments = data || [];
            this.state.unmatchedCount = this.state.payments.length;
        } catch (err) {
            console.error("Failed to load incoming payments", err);
            this.notification.add(_t("Could not retrieve incoming payments: ") + (err.message || err), { type: "danger" });
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
            cancel: () => {},
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
        this._stopCountdownTimer();
        const line = this.paymentLine;
        if (!line) return;

        line.seerbit_transaction_ref = transaction.reference;
        line.seerbit_payer_name = transaction.payer_name;
        line.seerbit_va_transaction_id = transaction.transaction_id;
        line.seerbit_is_matched = true;
        // Populate standard Odoo fields so references show up on orders, payment forms, and reports
        line.transaction_id = transaction.reference;
        line.payment_ref_no = transaction.reference;
        line.cardholder_name = transaction.payer_name || "Bank Transfer";
        line.card_type = "Seerbit VA";
        if (line.setReceiptInfo) {
            line.setReceiptInfo(`\nBank Transfer Ref: ${transaction.reference}\nPayer: ${transaction.payer_name || 'N/A'}`);
        }
        if (line.setPaymentStatus) {
            line.setPaymentStatus("done");
        } else if (line.set_payment_status) {
            line.set_payment_status("done");
        }

        this.notification.add(
            _t("Payment of %s confirmed from %s!", transaction.amount, transaction.payer_name),
            { type: "success" }
        );

        if (this.props.close) {
            this.props.close();
        }

        const order = this.pos?.getOrder ? this.pos.getOrder() : this.pos?.get_order?.();
        if (order && (order.isPaid ? order.isPaid() : order.is_paid?.())) {
            if (this.props.validateOrder) {
                this.props.validateOrder();
            } else if (this.pos?.validateOrder) {
                this.pos.validateOrder();
            }
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

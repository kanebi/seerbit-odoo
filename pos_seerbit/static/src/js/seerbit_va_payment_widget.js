/** @odoo-module **/

import { Component, useState, onMounted, onWillUnmount } from "@odoo/owl";
import { usePos } from "@point_of_sale/app/hooks/pos_hook";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { SeerbitIncomingPaymentsPopup } from "./seerbit_incoming_payments_popup";

export class SeerbitVaPaymentWidget extends Component {
    static template = "pos_seerbit.SeerbitVaPaymentWidget";
    static props = {
        paymentLine: { type: Object, optional: true },
        validateOrder: { type: Function, optional: true },
    };

    setup() {
        this.pos = usePos();
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        this.orm = useService("orm");
        this.state = useState({
            copied: false,
            loading: false,
            unmatchedCount: 0,
            dynamicVa: null,
            generatingDynamicVa: false,
            error: null,
            remainingSeconds: 0,
            isExpired: false,
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

    get hasGeneratedDynamicVa() {
        if (!this.isDynamic) {
            return true;
        }
        return Boolean(this.state.dynamicVa?.account_number || this.paymentLine?.seerbit_dynamic_account_number);
    }

    async generateDynamicVa() {
        const amount = this.paymentLine ? (this.paymentLine.getAmount ? this.paymentLine.getAmount() : this.paymentLine.amount || 0) : 0;
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
        if (this.paymentLine) {
            this.paymentLine.seerbit_dynamic_account_number = null;
            this.paymentLine.seerbit_dynamic_bank_name = null;
            this.paymentLine.seerbit_dynamic_account_name = null;
            this.paymentLine.seerbit_dynamic_va_id = null;
            this.paymentLine.seerbit_dynamic_reference = null;
            this.paymentLine.seerbit_dynamic_expires_at = null;
        }
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
        return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
    }

    get paymentLine() {
        return this.props.paymentLine || (this.pos.getOrder ? this.pos.getOrder()?.getSelectedPaymentline?.() : this.pos.get_order()?.get_selected_paymentline());
    }

    get paymentMethod() {
        return this.paymentLine?.payment_method_id;
    }

    get isMatched() {
        return Boolean(
            this.paymentLine?.seerbit_is_matched ||
            this.paymentLine?.seerbit_va_transaction_id ||
            this.paymentLine?.seerbit_transaction_ref ||
            this.paymentLine?.payment_status === "done"
        );
    }

    get isDynamic() {
        return this.paymentMethod?.seerbit_transfer_type === 'dynamic';
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
            return this.state.dynamicVa?.account_name || this.paymentLine?.seerbit_dynamic_account_name || this.pos.config.name;
        }
        return this.paymentMethod?.seerbit_va_name || this.pos.config.name;
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
            const order = this.pos.getOrder ? this.pos.getOrder() : this.pos.get_order();
            const orderRef = (order?.pos_reference || order?.name || `ORD_${Date.now()}`).replace(/[^a-zA-Z0-9_-]/g, "");
            const amount = this.paymentLine ? (this.paymentLine.getAmount ? this.paymentLine.getAmount() : this.paymentLine.amount || 0) : 0;
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

    get expectedAmountFormatted() {
        const amount = this.paymentLine ? (this.paymentLine.getAmount ? this.paymentLine.getAmount() : this.paymentLine.amount || 0) : 0;
        return this.env.utils.formatCurrency(amount);
    }

    async copyAccountNumber() {
        try {
            await navigator.clipboard.writeText(this.accountNumber);
            this.state.copied = true;
            setTimeout(() => (this.state.copied = false), 2500);
            this.notification.add(_t("Account number copied!"), { type: "info" });
        } catch (err) {
            console.error("Clipboard copy failed:", err);
        }
    }

    async openIncomingPaymentsModal() {
        const order = this.pos.getOrder ? this.pos.getOrder() : this.pos.get_order();
        const amount = this.paymentLine ? (this.paymentLine.getAmount ? this.paymentLine.getAmount() : this.paymentLine.amount || 0) : 0;
        this.dialog.add(SeerbitIncomingPaymentsPopup, {
            order: order,
            paymentLine: this.paymentLine,
            vaId: this.vaId,
            accountNumber: this.accountNumber,
            expectedAmount: amount,
            onPaymentConfirmed: (transaction) => this._onPaymentConfirmed(transaction),
        });
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

        const order = this.pos.getOrder ? this.pos.getOrder() : this.pos.get_order();
        if (order && (order.isPaid ? order.isPaid() : order.is_paid?.())) {
            if (this.props.validateOrder) {
                this.props.validateOrder();
            } else if (this.pos.validateOrder) {
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

/** @odoo-module **/

import { Component, useState, onMounted, onWillUnmount } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";

export class SeerbitIncomingPaymentsPopup extends Component {
    static template = "pos_seerbit.SeerbitIncomingPaymentsPopup";
    static components = { Dialog };
    static props = {
        close: Function,
        order: Object,
        paymentLine: Object,
        vaId: { type: [Number, Array, Object], optional: true },
        accountNumber: { type: String, optional: true },
        expectedAmount: { type: Number, optional: true },
        onPaymentConfirmed: Function,
    };

    setup() {
        this.orm = useService("orm");
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        this.busService = useService("bus_service");
        this.state = useState({
            payments: [],
            loading: true,
            syncing: false,
            searchQuery: "",
            selectedPayment: null,
            showTransferModal: false,
            transferPaymentTarget: null,
            availableTills: [],
            loadingTills: false,
            selectedTargetTillId: null,
            selectedTargetPmId: null,
            transferringPayment: false,
        });

        this.unsubscribeBus = null;
        this.quietSpoolerInterval = null;
        this._isSpoolingQuietly = false;

        onMounted(() => {
            this.fetchPayments();
            this._startQuietSpooler();
            if (this.busService) {
                this.unsubscribeBus = this.busService.subscribe("seerbit_va_notification", (payload) => {
                    const { type, data } = payload || {};
                    if (!data) return;
                    const clean = (s) => String(s || "").trim();
                    const isForThis = !data.account_number || clean(data.account_number) === clean(this.props.accountNumber);
                    if ((type === "seerbit_va_payment_received" || type === "seerbit_va_payment_matched") && isForThis) {
                        if (data && data.transaction_id) {
                            const existingIdx = this.state.payments.findIndex(
                                (p) => p.id === data.transaction_id || p.reference === data.reference
                            );
                            const paymentObj = {
                                id: data.transaction_id,
                                reference: data.reference,
                                payer_name: data.payer_name || "Bank Customer",
                                amount: data.amount,
                                payment_date: data.payment_date || new Date().toISOString(),
                                status: "successful",
                                matching_status: "unmatched",
                                has_duplicate_amount: Boolean(data.has_multiple_same_amount),
                                is_transferred_in: Boolean(data.is_transferred),
                                transferred_to_config_name: false,
                            };
                            if (existingIdx >= 0) {
                                this.state.payments[existingIdx] = Object.assign({}, this.state.payments[existingIdx], paymentObj);
                            } else {
                                this.state.payments.unshift(paymentObj);
                            }
                        }
                        this.fetchPayments();
                    }
                });
            }
        });

        onWillUnmount(() => {
            this._stopQuietSpooler();
            if (typeof this.unsubscribeBus === "function") {
                this.unsubscribeBus();
            }
        });
    }

    get vaId() {
        const v = this.props.vaId;
        const id = Array.isArray(v) ? v[0] : (typeof v === "object" && v !== null ? v.id : v);
        return id ? parseInt(id, 10) : null;
    }

    get filteredPayments() {
        const query = this.state.searchQuery.toLowerCase().trim();
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
        if (!this.props.expectedAmount) return false;
        return Math.abs(parseFloat(amount) - parseFloat(this.props.expectedAmount)) < 0.01;
    }

    async fetchPayments() {
        if (!this.vaId && !this.props.accountNumber) return;
        this.state.loading = true;
        try {
            const orderCreatedAt = this.props.order?.date_order || this.props.order?.creation_date?.toISOString?.() || new Date().toISOString();
            const data = await this.orm.call(
                "seerbit.va.transaction",
                "get_unmatched_payments_for_pos",
                [this.vaId, orderCreatedAt, 30, this.props.accountNumber]
            );
            this.state.payments = data || [];
        } catch (err) {
            const errMsg = String(err?.message || err || "");
            if (!errMsg.toLowerCase().includes("destroyed")) {
                console.error("Failed to load incoming payments", err);
                this.notification.add(_t("Could not retrieve incoming payments: ") + errMsg, { type: "danger" });
            }
        } finally {
            this.state.loading = false;
        }
    }

    async syncLiveFromSeerbit() {
        if (!this.vaId) return;
        this.state.syncing = true;
        // 1. Immediately fetch from local Odoo DB first (sub-50ms)
        await this.fetchPayments();
        try {
            const timeoutPromise = new Promise((_, reject) =>
                setTimeout(() => reject(new Error("SeerBit live sync timed out after 8s")), 8000)
            );
            await Promise.race([
                this.orm.call(
                    "seerbit.virtual.account",
                    "action_refresh_balance",
                    [[this.vaId]]
                ),
                timeoutPromise,
            ]);
            await this.fetchPayments();
            this.notification.add(_t("Payments refreshed!"), { type: "success" });
        } catch (err) {
            console.warn("Live SeerBit refresh warning:", err);
            await this.fetchPayments();
            const errMsg = err?.data?.message || err?.message || String(err);
            this.notification.add(_t("Could not refresh live from Seerbit: ") + errMsg, { type: "warning" });
        } finally {
            this.state.syncing = false;
        }
    }

    _startQuietSpooler() {
        this._stopQuietSpooler();
        if (!this.vaId) return;

        this._spoolQuietly();
        this.quietSpoolerInterval = setInterval(() => {
            this._spoolQuietly();
        }, 5000);
    }

    _stopQuietSpooler() {
        if (this.quietSpoolerInterval) {
            clearInterval(this.quietSpoolerInterval);
            this.quietSpoolerInterval = null;
        }
    }

    async _spoolQuietly() {
        if (!this.vaId || this._isSpoolingQuietly) return;
        this._isSpoolingQuietly = true;
        try {
            const timeoutPromise = new Promise((_, reject) =>
                setTimeout(() => reject(new Error("Quiet sync timed out")), 6000)
            );
            await Promise.race([
                this.orm.call(
                    "seerbit.virtual.account",
                    "action_refresh_balance",
                    [[this.vaId]]
                ),
                timeoutPromise,
            ]);
        } catch (err) {
            console.debug("Quiet Seerbit background spool warning:", err);
        }

        try {
            const orderCreatedAt = this.props.order?.date_order || this.props.order?.creation_date?.toISOString?.() || new Date().toISOString();
            const data = await this.orm.call(
                "seerbit.va.transaction",
                "get_unmatched_payments_for_pos",
                [this.vaId, orderCreatedAt, 30, this.props.accountNumber]
            );
            this.state.payments = data || [];
        } catch (err) {
            console.debug("Quiet payments fetch warning:", err);
        } finally {
            this._isSpoolingQuietly = false;
        }
    }

    async openTransferToTill(payment) {
        this.state.transferPaymentTarget = payment;
        this.state.showTransferModal = true;
        this.state.loadingTills = true;
        this.state.selectedTargetTillId = null;
        this.state.selectedTargetPmId = null;
        try {
            const currentConfigId = this.env?.services?.pos?.config?.id;
            const tills = await this.orm.call(
                "seerbit.va.transaction",
                "get_available_tills_for_transfer",
                [],
                { current_config_id: currentConfigId }
            );
            this.state.availableTills = tills || [];
            if (this.state.availableTills.length === 1) {
                this.selectTargetTill(this.state.availableTills[0]);
            }
        } catch (err) {
            console.error("Failed to load available tills:", err);
            this.notification.add(_t("Failed to load available tills: ") + (err.message || err), { type: "danger" });
        } finally {
            this.state.loadingTills = false;
        }
    }

    selectTargetTill(till) {
        this.state.selectedTargetTillId = till.id;
        if (till.payment_methods?.length === 1) {
            this.state.selectedTargetPmId = till.payment_methods[0].id;
        } else {
            this.state.selectedTargetPmId = till.payment_methods?.[0]?.id || null;
        }
    }

    closeTransferModal() {
        this.state.showTransferModal = false;
        this.state.transferPaymentTarget = null;
        this.state.selectedTargetTillId = null;
        this.state.selectedTargetPmId = null;
    }

    async confirmTransferToTill() {
        const payment = this.state.transferPaymentTarget;
        const targetConfigId = this.state.selectedTargetTillId;
        const targetPmId = this.state.selectedTargetPmId;
        if (!payment || !targetConfigId) {
            this.notification.add(_t("Please select a destination till."), { type: "warning" });
            return;
        }

        this.state.transferringPayment = true;
        try {
            const res = await this.orm.call(
                "seerbit.va.transaction",
                "transfer_payment_to_till",
                [payment.id, targetConfigId, targetPmId]
            );
            if (res.success) {
                this.notification.add(
                    _t("Payment of %s successfully transferred to %s!", this.formatAmount(payment.amount), res.dest_till_name),
                    { type: "success" }
                );
                this.closeTransferModal();
                this.state.payments = this.state.payments.filter((p) => p.id !== payment.id);
                await this.fetchPayments();
            } else {
                this.notification.add(res.error || _t("Failed to transfer payment to till."), { type: "danger" });
            }
        } catch (err) {
            console.error("Transfer error:", err);
            this.notification.add(_t("Error transferring payment: ") + (err.message || err), { type: "danger" });
        } finally {
            this.state.transferringPayment = false;
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
            const orderUuid = this.props.order?.uuid || this.props.order?.pos_reference || this.props.order?.name || String(this.props.order?.id);
            const res = await this.orm.call(
                "seerbit.va.transaction",
                "match_payment_to_pos_order_atomic",
                [payment.id, orderUuid, false, "manual_matched"]
            );
            if (res.success) {
                this.props.onPaymentConfirmed(res);
                this.props.close();
            } else {
                this.notification.add(res.error || _t("Payment selection failed."), { type: "danger" });
                await this.fetchPayments();
            }
        } catch (err) {
            this.notification.add(_t("Error matching payment: ") + (err.message || err), { type: "danger" });
        }
    }
}


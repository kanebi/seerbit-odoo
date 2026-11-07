/** @odoo-module **/

import { Component, useState, onMounted } from "@odoo/owl";
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
        vaId: { type: Number, optional: true },
        accountNumber: { type: String, optional: true },
        expectedAmount: { type: Number, optional: true },
        onPaymentConfirmed: Function,
    };

    setup() {
        this.orm = useService("orm");
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        this.state = useState({
            payments: [],
            loading: true,
            syncing: false,
            searchQuery: "",
            selectedPayment: null,
        });

        onMounted(() => {
            this.fetchPayments();
        });
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
        this.state.loading = true;
        try {
            const orderCreatedAt = this.props.order?.date_order || this.props.order?.creation_date?.toISOString?.() || new Date().toISOString();
            const data = await this.orm.call(
                "seerbit.va.transaction",
                "get_unmatched_payments_for_pos",
                [this.props.vaId, orderCreatedAt, 30]
            );
            this.state.payments = data || [];
        } catch (err) {
            console.error("Failed to load incoming payments", err);
            this.notification.add(_t("Could not retrieve incoming payments: ") + (err.message || err), { type: "danger" });
        } finally {
            this.state.loading = false;
        }
    }

    async syncLiveFromSeerbit() {
        if (!this.props.vaId) return;
        this.state.syncing = true;
        try {
            await this.orm.call(
                "seerbit.virtual.account",
                "action_refresh_balance",
                [[this.props.vaId]]
            );
            await this.fetchPayments();
            this.notification.add(_t("Payments refreshed!"), { type: "success" });
        } catch (err) {
            this.notification.add(_t("Could not refresh: ") + (err.message || err), { type: "warning" });
        } finally {
            this.state.syncing = false;
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
            const orderUuid = this.props.order?.uuid || this.props.order?.pos_reference || this.props.order?.name || String(this.props.order?.id);
            const res = await this.orm.call(
                "seerbit.va.transaction",
                "match_payment_to_pos_order_atomic",
                [payment.id, orderUuid]
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


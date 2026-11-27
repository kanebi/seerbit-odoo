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
        balanceRequired: { type: Number, optional: true },
        isReopeningPartial: { type: Boolean, optional: true },
        onPaymentConfirmed: Function,
        "*": true,
    };

    setup() {
        this.orm = useService("orm");
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        this.busService = useService("bus_service");
        const line = this.props.paymentLine;
        const isReopening = Boolean(this.props.isReopeningPartial || line?.seerbit_is_partially_matched);
        const settledSoFar = Number(line?.seerbit_matched_amount || 0);
        let balanceRequired = Number(this.props.balanceRequired || 0);
        if (isReopening && !balanceRequired) {
            const order = this.props.order || (this.pos?.getOrder ? this.pos.getOrder() : this.pos?.get_order?.());
            const currentDue = order?.get_due ? order.get_due() : (order?.totalDue !== undefined ? order.totalDue : 0);
            balanceRequired = Math.max(0, currentDue);
        }

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
            // Partial matching state
            isPartiallyMatched: isReopening,
            settledAmount: settledSoFar,
            initialSettledAmount: settledSoFar,
            balanceRequired: balanceRequired,
            currentExpectedAmount: isReopening ? balanceRequired : (this.props.expectedAmount || 0),
            matchedTransactions: (line && line.seerbit_matched_transactions) ? [...line.seerbit_matched_transactions] : [],
            // Multi-Till Payment Tabs & Filter
            activePaymentTab: "till", // 'till' | 'all'
            selectedTillPmFilter: null,
            allPayments: [],
            tillPms: [],
            loadingAllPayments: false,
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
        let list = [];
        if (this.state.activePaymentTab === "till") {
            list = this.state.payments;
        } else {
            list = this.state.allPayments;
            if (this.state.selectedTillPmFilter !== null) {
                const f = this.state.selectedTillPmFilter;
                list = list.filter((p) =>
                    p.till_key === f ||
                    p.till_pm_id === f ||
                    String(p.till_pm_id) === String(f) ||
                    p.till_config_id === f ||
                    String(p.till_config_id) === String(f)
                );
            }
        }
        if (!query) return list;
        return list.filter((p) =>
            (p.payer_name && p.payer_name.toLowerCase().includes(query)) ||
            (p.reference && p.reference.toLowerCase().includes(query)) ||
            (p.till_label && p.till_label.toLowerCase().includes(query)) ||
            (String(p.amount).includes(query))
        );
    }

    async setPaymentTab(tab) {
        this.state.activePaymentTab = tab;
        if (tab === "all") {
            await this.fetchAllPayments();
        }
    }

    filterByTillPm(pmId) {
        this.state.selectedTillPmFilter = pmId;
    }

    get selectedTillPmLabel() {
        if (this.state.selectedTillPmFilter === null || this.state.selectedTillPmFilter === undefined) {
            return "All Tills (All PMs)";
        }
        const found = (this.state.tillPms || []).find(
            (tpm) => (tpm.key || tpm.id) === this.state.selectedTillPmFilter || tpm.id === this.state.selectedTillPmFilter
        );
        return found ? (found.label || (found.config_name + ": " + found.pm_name)) : "Selected Till PM";
    }



    async fetchAllPayments() {
        this.state.loadingAllPayments = true;
        try {
            const currentConfigId = this.props.order?.config_id?.id || this.props.paymentLine?.pos?.config?.id || null;
            const res = await this.orm.call(
                "seerbit.va.transaction",
                "get_all_unmatched_payments_for_pos",
                [],
                { current_config_id: currentConfigId, limit: 100 }
            );
            this.state.tillPms = res.till_pms || [];
            this.state.allPayments = res.payments || [];
        } catch (err) {
            console.error("Failed to fetch all unmatched payments:", err);
        } finally {
            this.state.loadingAllPayments = false;
        }
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

    get lineMatchedCount() {
        return this.props.paymentLine?.seerbit_matched_count || this.state.matchedTransactions.length || 1;
    }

    get expectedAmount() {
        if (this.state.isPartiallyMatched && this.state.currentExpectedAmount !== undefined) {
            return this.state.currentExpectedAmount;
        }
        return this.props.expectedAmount || 0;
    }

    isExactAmount(amount) {
        if (!this.expectedAmount) return false;
        return Math.abs(parseFloat(amount) - parseFloat(this.expectedAmount)) < 0.01;
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
        if (this.state.activePaymentTab === "all") {
            await this.fetchAllPayments();
        }
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
            if (this.state.activePaymentTab === "all") {
                await this.fetchAllPayments();
            }
            this.notification.add(_t("Payments refreshed!"), { type: "success" });
        } catch (err) {
            console.warn("Live SeerBit refresh warning:", err);
            await this.fetchPayments();
            if (this.state.activePaymentTab === "all") {
                await this.fetchAllPayments();
            }
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
        const isFromOtherTill = payment.till_label && !payment.is_current_till;
        const confirmMsg = isFromOtherTill
            ? _t(`Confirm payment of ${amountFormatted} from "${payment.payer_name}" (Auto-transfers from ${payment.till_label})?`)
            : _t(`Confirm payment of ${amountFormatted} from "${payment.payer_name}"?`);
        this.dialog.add(ConfirmationDialog, {
            title: _t("Confirm Payment"),
            body: confirmMsg,
            confirm: async () => {
                await this._executePaymentSelection(payment);
            },
            cancel: () => { },
        });
    }

    async _executePaymentSelection(payment) {
        try {
            const orderUuid = this.props.order?.uuid || this.props.order?.pos_reference || this.props.order?.name || String(this.props.order?.id);
            const currentConfigId = this.props.order?.config_id?.id || this.props.paymentLine?.pos?.config?.id || null;
            const res = await this.orm.call(
                "seerbit.va.transaction",
                "match_payment_to_pos_order_atomic",
                [payment.id, orderUuid, false, "manual_matched", currentConfigId]
            );
            if (res.success) {
                const payAmt = Number(payment.amount);
                const currentExpected = this.expectedAmount;
                const line = this.props.paymentLine;

                const txRecord = {
                    id: res.transaction_id || payment.id,
                    reference: res.reference || payment.reference,
                    amount: payAmt,
                    payer_name: payment.payer_name,
                };

                if (payAmt < currentExpected - 0.01) {
                    // Partial payment matched! Settle exact amount on line and keep modal open
                    this.state.settledAmount += payAmt;
                    this.state.matchedTransactions.push(txRecord);
                    this.state.isPartiallyMatched = true;
                    this.state.currentExpectedAmount = Math.max(0, currentExpected - payAmt);

                    if (line) {
                        line.seerbit_matched_amount = this.state.settledAmount;
                        line.seerbit_is_partially_matched = true;
                        line.seerbit_matched_count = (line.seerbit_matched_count || 0) + 1;
                        line.seerbit_matched_transactions = [...this.state.matchedTransactions];
                        line.seerbit_transaction_ref = res.reference || payment.reference;
                        line.seerbit_payer_name = payment.payer_name;
                        line.transaction_id = res.reference || payment.reference;
                        line.payment_ref_no = res.reference || payment.reference;
                        line.cardholder_name = payment.payer_name || line.cardholder_name || "Bank Transfer";
                        line.card_type = "Seerbit VA";

                        // Immediately update amount on line to settled so far
                        if (line.set_amount) {
                            line.set_amount(this.state.settledAmount);
                        } else if (line.setAmount) {
                            line.setAmount(this.state.settledAmount);
                        } else {
                            line.amount = this.state.settledAmount;
                        }

                        // Set payment_status to "done" so POS displays "Payment Successful"
                        if (line.set_payment_status) {
                            line.set_payment_status("done");
                        } else if (line.setPaymentStatus) {
                            line.setPaymentStatus("done");
                        } else {
                            line.payment_status = "done";
                        }

                        const receiptText = `\nBank Transfer (${line.seerbit_matched_count}): Ref ${txRecord.reference} - ${this.formatAmount(payAmt)} (Payer: ${txRecord.payer_name || 'N/A'})`;
                        if (line.set_receipt_info) {
                            line.set_receipt_info(receiptText);
                        } else if (line.setReceiptInfo) {
                            line.setReceiptInfo(receiptText);
                        } else {
                            line.ticket = (line.ticket || '') + receiptText;
                        }
                    }

                    this.notification.add(
                        _t("Partial payment of %s matched! Remaining balance: %s", this.formatAmount(payAmt), this.formatAmount(this.state.currentExpectedAmount)),
                        { type: "info" }
                    );

                    // Keep modal open and refresh lists
                    await this.fetchPayments();
                    if (this.state.activePaymentTab === "all") {
                        await this.fetchAllPayments();
                    }
                } else {
                    // Full or completing payment matched!
                    const amountToSettle = Math.min(payAmt, currentExpected);
                    const overpaymentAmount = Math.max(0, payAmt - currentExpected);

                    this.state.settledAmount += amountToSettle;
                    this.state.matchedTransactions.push(txRecord);
                    if (line) {
                        line.seerbit_matched_amount = this.state.settledAmount;
                        line.seerbit_overpaid_amount = overpaymentAmount;
                        line.seerbit_is_partially_matched = false;
                        line.seerbit_matched_count = (line.seerbit_matched_count || 0) + 1;
                        line.seerbit_matched_transactions = [...this.state.matchedTransactions];
                        line.seerbit_is_matched = true;
                        if (line.set_amount) {
                            line.set_amount(this.state.settledAmount);
                        } else if (line.setAmount) {
                            line.setAmount(this.state.settledAmount);
                        } else {
                            line.amount = this.state.settledAmount;
                        }
                        if (line.set_payment_status) {
                            line.set_payment_status("done");
                        } else if (line.setPaymentStatus) {
                            line.setPaymentStatus("done");
                        } else {
                            line.payment_status = "done";
                        }

                        if (line.seerbit_matched_transactions && line.seerbit_matched_transactions.length > 1) {
                            let receiptText = "\n--- SeerBit Bank Transfers ---";
                            line.seerbit_matched_transactions.forEach((tx, idx) => {
                                receiptText += `\nTransfer (${idx + 1}/${line.seerbit_matched_transactions.length}): Ref ${tx.reference || 'N/A'} - ${this.formatAmount(tx.amount)} (Payer: ${tx.payer_name || 'N/A'})`;
                            });
                            if (line.set_receipt_info) {
                                line.set_receipt_info(receiptText);
                            } else if (line.setReceiptInfo) {
                                line.setReceiptInfo(receiptText);
                            } else {
                                line.ticket = (line.ticket || '') + receiptText;
                            }
                        }
                    }
                    if (overpaymentAmount > 0.01) {
                        this.notification.add(
                            _t("Payment of %s matched with overpayment of %s! Order settled for %s.",
                               this.formatAmount(payAmt),
                               this.formatAmount(overpaymentAmount),
                               this.formatAmount(amountToSettle)),
                            { type: "warning" }
                        );
                    }
                    this.props.onPaymentConfirmed(res);
                    this.props.close();
                }
            } else {
                this.notification.add(res.error || _t("Payment selection failed."), { type: "danger" });
                await this.fetchPayments();
                if (this.state.activePaymentTab === "all") {
                    await this.fetchAllPayments();
                }
            }
        } catch (err) {
            this.notification.add(_t("Error matching payment: ") + (err.message || err), { type: "danger" });
        }
    }

    closePopup() {
        const line = this.props.paymentLine;
        if (line) {
            if (line.seerbit_is_partially_matched || this.state.isPartiallyMatched) {
                const finalSettled = this.state.settledAmount > 0 ? this.state.settledAmount : (line.seerbit_matched_amount || 0);
                if (line.set_amount) {
                    line.set_amount(finalSettled);
                } else if (line.setAmount) {
                    line.setAmount(finalSettled);
                } else {
                    line.amount = finalSettled;
                }
                line.seerbit_matched_amount = finalSettled;
                line.seerbit_is_partially_matched = true;
                if (finalSettled > 0) {
                    if (line.set_payment_status) {
                        line.set_payment_status("done");
                    } else if (line.setPaymentStatus) {
                        line.setPaymentStatus("done");
                    } else {
                        line.payment_status = "done";
                    }
                } else {
                    if (line.set_payment_status) {
                        line.set_payment_status(null);
                    } else if (line.setPaymentStatus) {
                        line.setPaymentStatus(null);
                    } else {
                        line.payment_status = null;
                    }
                }
            }
        }
        this._stopQuietSpooler();
        if (this.props.close) {
            this.props.close();
        }
    }
}


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
        autoGenerate: { type: Boolean, optional: true },
        validateOrder: { type: Function, optional: true },
        close: { type: Function, optional: true },
        balanceRequired: { type: Number, optional: true },
        isReopeningPartial: { type: Boolean, optional: true },
        "*": true,
    };

    setup() {
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        this.orm = useService("orm");
        this.busService = useService("bus_service");

        const shouldAutoGenerate = Boolean(
            (this.props.autoGenerate || this.paymentLine?.seerbit_auto_generate) &&
            !this.paymentLine?.seerbit_amount_changed &&
            !this.paymentLine?.seerbit_dynamic_account_number &&
            this.isDynamic
        );

        const line = this.paymentLine;
        const isReopening = Boolean(this.props.isReopeningPartial || line?.seerbit_is_partially_matched);
        const settledSoFar = Number(line?.seerbit_matched_amount || 0);
        let balanceRequired = Number(this.props.balanceRequired || 0);
        if (isReopening && !balanceRequired) {
            const order = this.pos?.getOrder ? this.pos.getOrder() : this.pos?.get_order?.();
            const currentDue = order?.get_due ? order.get_due() : (order?.totalDue !== undefined ? order.totalDue : 0);
            balanceRequired = Math.max(0, currentDue);
        }

        this.state = useState({
            copied: false,
            generatingDynamicVa: shouldAutoGenerate,
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
            currentExpectedAmount: isReopening ? balanceRequired : (this.paymentLine ? (this.paymentLine.getAmount ? this.paymentLine.getAmount() : this.paymentLine.amount || 0) : 0),
            matchedTransactions: [],
            // Multi-Till Payment Tabs & Filter
            activePaymentTab: "till", // 'till' | 'all'
            selectedTillPmFilter: null,
            allPayments: [],
            tillPms: [],
            loadingAllPayments: false,
        });

        this.countdownInterval = null;
        this.successTimer = null;
        this.statusPoller = null;
        this._busHandler = null;
        this.quietSpoolerInterval = null;
        this._isSpoolingQuietly = false;

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
                if (this.paymentLine?.seerbit_is_matched && !this.state.isSuccess && !this.state.isPartiallyMatched) {
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
                } else if (shouldAutoGenerate) {
                    if (this.paymentLine) {
                        this.paymentLine.seerbit_auto_generate = false;
                    }
                    this.generateDynamicVa();
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

                    const isAutoMatchEligible = (type === "seerbit_va_payment_matched") ||
                        (type === "seerbit_va_payment_received" && isForThisAccount && (
                            this.isDynamic || (!data.has_multiple_same_amount && !data.requires_manual_match && isExactAmt)
                        ));

                    if (isAutoMatchEligible) {
                        this.showSuccessAndClose(data);
                    } else if (isForThisAccount) {
                        // Real-time optimistic update of payments table so incoming payment shows immediately
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
                            this.state.unmatchedCount = this.state.payments.length;
                        }
                        this.fetchPayments();
                        this._checkPendingPaymentsCount();
                    }
                };
                this.busService.subscribe("seerbit_va_notification", this._busHandler);
            }
        });

        onWillUnmount(() => {
            this._stopCountdownTimer();
            this._stopQuietSpooler();
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
        return this.props.paymentLine || (this.pos?.getOrder ? this.pos.getOrder()?.getSelectedPaymentline?.() : this.pos?.get_order?.()?.get_selected_paymentline?.());
    }

    get pos() {
        return this.props.pos || this.env.services.pos;
    }

    get paymentMethod() {
        const pm = this.paymentLine?.payment_method_id;
        if (pm && typeof pm === "object") {
            return pm;
        }
        if (typeof pm === "number" && this.pos?.models) {
            return this.pos.models["pos.payment.method"]?.get?.(pm) || this.pos.payment_methods?.find?.(m => m.id === pm);
        }
        return pm || null;
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
        let id = Array.isArray(va) ? va[0] : (typeof va === "object" && va !== null ? va.id : va);
        if (!id && this.paymentMethod?.id && this.pos?.models) {
            const pmRec = this.pos.models["pos.payment.method"]?.get?.(this.paymentMethod.id);
            if (pmRec?.seerbit_va_id) {
                const pva = pmRec.seerbit_va_id;
                id = Array.isArray(pva) ? pva[0] : (typeof pva === "object" && pva !== null ? pva.id : pva);
            }
        }
        return id ? parseInt(id, 10) : null;
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
            return this.state.dynamicVa?.account_name || this.paymentLine?.seerbit_dynamic_account_name || this.pos?.company?.name || this.pos?.config?.name || "Store Account";
        }
        return this.paymentMethod?.seerbit_va_name || this.pos?.company?.name || this.pos?.config?.name || "Store Account";
    }

    get expectedAmount() {
        if (this.state?.isPartiallyMatched && this.state?.currentExpectedAmount !== undefined) {
            return this.state.currentExpectedAmount;
        }
        return this.paymentLine ? (this.paymentLine.getAmount ? this.paymentLine.getAmount() : this.paymentLine.amount || 0) : 0;
    }

    get originalExpectedAmount() {
        return this.paymentLine ? (this.paymentLine.getAmount ? this.paymentLine.getAmount() : this.paymentLine.amount || 0) : 0;
    }

    get lineMatchedCount() {
        return this.paymentLine?.seerbit_matched_count || this.state.matchedTransactions.length || 1;
    }

    get expectedAmountFormatted() {
        return this.env.utils.formatCurrency(this.expectedAmount);
    }

    get filteredPayments() {
        const query = this.state.paymentsSearchQuery.toLowerCase().trim();
        let list = [];
        if (this.state.activePaymentTab === "till") {
            list = this.state.payments;
        } else {
            list = this.state.allPayments;
            if (this.state.selectedTillPmFilter !== null) {
                list = list.filter((p) => p.till_pm_id === this.state.selectedTillPmFilter);
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

    async fetchAllPayments() {
        this.state.loadingAllPayments = true;
        try {
            const currentConfigId = this.pos?.config?.id || null;
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
        const previousAmount = this.paymentLine?.seerbit_dynamic_amount;
        const amountChanged = previousAmount && Math.abs(parseFloat(previousAmount) - parseFloat(amount)) > 0.01;

        if (this.state.isExpired || amountChanged || !this.paymentLine?.seerbit_dynamic_account_number) {
            this.resetDynamicVa();
        }
        await this._initDynamicVa();
    }

    onChangeAmount() {
        if (this.paymentLine) {
            this.paymentLine.seerbit_amount_changed = true;
            this.paymentLine.seerbit_auto_generate = false;
            if (typeof this.paymentLine.set_amount === "function") {
                this.paymentLine.set_amount(0);
            } else if (typeof this.paymentLine.setAmount === "function") {
                this.paymentLine.setAmount(0);
            } else {
                this.paymentLine.amount = 0;
            }
        }
        this.resetDynamicVa();
        this._closeModal();
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
            this.paymentLine.seerbit_dynamic_amount = null;
        }
    }

    async _initDynamicVa() {
        const amount = this.expectedAmount;
        const previousAmount = this.paymentLine?.seerbit_dynamic_amount;
        const amountChanged = previousAmount && Math.abs(parseFloat(previousAmount) - parseFloat(amount)) > 0.01;

        if (!amountChanged && this.paymentLine?.seerbit_dynamic_account_number && !this.state.isExpired) {
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
                this.paymentLine.seerbit_dynamic_amount = amount;
                this.paymentLine.seerbit_amount_changed = false;
                this.paymentLine.seerbit_auto_generate = false;
            }
            this._startCountdownTimer();
        } catch (err) {
            console.error("Failed to generate dynamic virtual account:", err);
            const errorMsg =
                err.data?.data?.arguments?.[0] ||
                err.data?.data?.message ||
                (err.data?.message && err.data.message !== "Odoo Server Error" ? err.data.message : null) ||
                err.message ||
                _t("Failed to generate dynamic Virtual Account.");
            this.state.error = errorMsg;
            this.notification.add(this.state.error, { type: "danger" });
        } finally {
            this.state.generatingDynamicVa = false;
        }
    }

    async togglePaymentsList() {
        this.state.showPaymentsList = !this.state.showPaymentsList;
        if (this.state.showPaymentsList) {
            if (!this.state.payments.length) {
                await this.fetchPayments();
            }
            this._startQuietSpooler();
        } else {
            this._stopQuietSpooler();
        }
    }

    _startQuietSpooler() {
        this._stopQuietSpooler();
        if (!this.vaId || this.state.isSuccess) return;

        // Immediately run quiet refresh on open
        this._spoolQuietly();

        // Continuously run in background every 5 seconds without disrupting UI or showing loaders
        this.quietSpoolerInterval = setInterval(() => {
            if (this.state.showPaymentsList && !this.state.isSuccess) {
                this._spoolQuietly();
            } else {
                this._stopQuietSpooler();
            }
        }, 5000);
    }

    _stopQuietSpooler() {
        if (this.quietSpoolerInterval) {
            clearInterval(this.quietSpoolerInterval);
            this.quietSpoolerInterval = null;
        }
    }

    async _spoolQuietly() {
        if ((!this.vaId && !this.accountNumber) || this.state.isSuccess || this._isSpoolingQuietly) return;
        this._isSpoolingQuietly = true;
        try {
            if (this.vaId) {
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
            }
        } catch (err) {
            console.debug("Quiet Seerbit background spool warning:", err);
        }

        try {
            const order = this.pos?.getOrder ? this.pos.getOrder() : this.pos?.get_order?.();
            const orderCreatedAt = order?.date_order || order?.creation_date?.toISOString?.() || new Date().toISOString();
            const data = await this.orm.call(
                "seerbit.va.transaction",
                "get_unmatched_payments_for_pos",
                [this.vaId, orderCreatedAt, 30, this.accountNumber]
            );
            if (!this.state.isSuccess && this.state.showPaymentsList) {
                this.state.payments = data || [];
                this.state.unmatchedCount = this.state.payments.length;
            }
        } catch (err) {
            console.debug("Quiet payments fetch warning:", err);
        } finally {
            this._isSpoolingQuietly = false;
        }
    }

    async openIncomingPaymentsModal() {
        await this.togglePaymentsList();
    }

    async fetchPayments() {
        if ((!this.vaId && !this.accountNumber) || this.state.isSuccess) return;
        this.state.loadingPayments = true;
        try {
            const order = this.pos?.getOrder ? this.pos.getOrder() : this.pos?.get_order?.();
            const orderCreatedAt = order?.date_order || order?.creation_date?.toISOString?.() || new Date().toISOString();
            const data = await this.orm.call(
                "seerbit.va.transaction",
                "get_unmatched_payments_for_pos",
                [this.vaId, orderCreatedAt, 30, this.accountNumber]
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
        if (!this.vaId && !this.accountNumber) return;
        this.state.syncingPayments = true;
        // 1. Immediately fetch from local Odoo DB first (sub-50ms)
        // so webhook/cron received payments display immediately without waiting for SeerBit external API
        await this.fetchPayments();
        try {
            if (this.vaId) {
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
            }
            await this.fetchPayments();
            this.notification.add(_t("Payments refreshed!"), { type: "success" });
        } catch (err) {
            console.warn("Live SeerBit refresh warning:", err);
            await this.fetchPayments();
            const errMsg = err?.data?.message || err?.message || String(err);
            this.notification.add(_t("Could not refresh live from Seerbit: ") + errMsg, { type: "warning" });
        } finally {
            this.state.syncingPayments = false;
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
            const order = this.pos?.getOrder ? this.pos.getOrder() : this.pos?.get_order?.();
            const orderUuid = order?.uuid || order?.pos_reference || order?.name || String(order?.id);
            const currentConfigId = this.pos?.config?.id || null;
            const res = await this.orm.call(
                "seerbit.va.transaction",
                "match_payment_to_pos_order_atomic",
                [payment.id, orderUuid, false, "manual_matched", currentConfigId]
            );
            if (res.success) {
                const payAmt = Number(payment.amount);
                const currentExpected = this.expectedAmount;
                const line = this.paymentLine;

                if (payAmt < currentExpected - 0.01) {
                    // Partial payment matched! Settle exact amount on line and keep modal open
                    this.state.settledAmount += payAmt;
                    this.state.matchedTransactions.push(res);
                    this.state.isPartiallyMatched = true;
                    this.state.currentExpectedAmount = Math.max(0, currentExpected - payAmt);

                    if (line) {
                        line.seerbit_matched_amount = this.state.settledAmount;
                        line.seerbit_is_partially_matched = true;
                        line.seerbit_matched_count = (line.seerbit_matched_count || 0) + 1;
                        line.seerbit_transaction_ref = res.reference || payment.reference;
                        line.seerbit_payer_name = payment.payer_name;
                        line.transaction_id = res.reference || payment.reference;
                        line.payment_ref_no = res.reference || payment.reference;
                    }

                    this.notification.add(
                        _t("Partial payment of %s matched! Remaining balance: %s", this.formatAmount(payAmt), this.formatAmount(this.state.currentExpectedAmount)),
                        { type: "info" }
                    );

                    // Keep modal open and refresh list
                    await this.fetchPayments();
                    if (this.state.activePaymentTab === "all") {
                        await this.fetchAllPayments();
                    }
                } else {
                    // Full or completing payment matched!
                    this.state.settledAmount += payAmt;
                    this.state.matchedTransactions.push(res);
                    if (line) {
                        line.seerbit_matched_amount = this.state.settledAmount;
                        line.seerbit_is_partially_matched = false;
                        line.seerbit_matched_count = (line.seerbit_matched_count || 0) + 1;
                        line.seerbit_is_matched = true;
                        if (line.set_amount) {
                            line.set_amount(this.state.settledAmount);
                        } else if (line.setAmount) {
                            line.setAmount(this.state.settledAmount);
                        } else {
                            line.amount = this.state.settledAmount;
                        }
                    }
                    this._onPaymentConfirmed(res);
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

    async openTransferToTill(payment) {
        this.state.transferPaymentTarget = payment;
        this.state.showTransferModal = true;
        this.state.loadingTills = true;
        this.state.selectedTargetTillId = null;
        this.state.selectedTargetPmId = null;
        try {
            const currentConfigId = this.pos?.config?.id;
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
                this.state.unmatchedCount = this.state.payments.length;
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

    _onPaymentConfirmed(transaction) {
        this.showSuccessAndClose(transaction);
    }

    showSuccessAndClose(data) {
        this._stopQuietSpooler();
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
            line.seerbit_matched_amount = data.amount || this.expectedAmount;
            if (!line.seerbit_is_partially_matched) {
                line.seerbit_matched_count = 1;
            }
            line.seerbit_is_partially_matched = false;
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
                if (line.set_receipt_info) {
                    line.set_receipt_info(receiptText);
                } else if (line.setReceiptInfo) {
                    line.setReceiptInfo(receiptText);
                } else {
                    line.ticket = (line.ticket || '') + receiptText;
                }
            }
            if (line.set_payment_status) {
                line.set_payment_status("done");
            } else if (line.setPaymentStatus) {
                line.setPaymentStatus("done");
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
        if (this._isFinishingSuccess) {
            return;
        }
        this._isFinishingSuccess = true;
        const line = this.paymentLine;

        if (this.statusPoller) {
            clearInterval(this.statusPoller);
            this.statusPoller = null;
        }
        if (this.successTimer) {
            clearInterval(this.successTimer);
            this.successTimer = null;
        }
        if (line) {
            line._seerbit_modal_open = false;
            delete line._seerbit_modal;
        }
        if (this.pos && this.pos._active_seerbit_modal === this) {
            this.pos._active_seerbit_modal = null;
        }

        const validateFn = this.props.validateOrder;
        const posRef = this.pos;

        try {
            if (this.props.close) {
                this.props.close();
            } else if (this.env.dialogData?.close) {
                this.env.dialogData.close();
            }
        } catch (e) {
            console.error("Error closing modal after success:", e);
        }

        // Auto-validate order if fully paid (no payment line with remaining balance)
        const order = posRef?.getOrder ? posRef.getOrder() : posRef?.get_order?.();
        if (order) {
            const isPaid = order.isPaid ? order.isPaid() : (order.is_paid ? order.is_paid() : false);
            const due = order.get_due ? order.get_due() : (order.totalDue !== undefined ? order.totalDue : 0);
            if ((isPaid || due <= 0.01) && due <= 0.01) {
                setTimeout(async () => {
                    try {
                        if (typeof validateFn === "function") {
                            await validateFn();
                        } else if (typeof posRef?.validateOrder === "function") {
                            await posRef.validateOrder();
                        } else if (typeof posRef?._active_payment_screen?.validateOrder === "function") {
                            await posRef._active_payment_screen.validateOrder();
                        }
                    } catch (e) {
                        console.error("Error auto-validating order after modal success:", e);
                    }
                }, 100);
            }
        }
    }

    _closeModal() {
        if (this.state.isSuccess) {
            return this._finishSuccess();
        }
        const line = this.paymentLine;
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
                if (line.set_payment_status) {
                    line.set_payment_status("waiting");
                } else if (line.setPaymentStatus) {
                    line.setPaymentStatus("waiting");
                }
            }
        }
        this._stopCountdownTimer();
        this._stopQuietSpooler();
        if (this.statusPoller) {
            clearInterval(this.statusPoller);
            this.statusPoller = null;
        }
        if (this.successTimer) {
            clearInterval(this.successTimer);
            this.successTimer = null;
        }
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

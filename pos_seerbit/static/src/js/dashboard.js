/** @odoo-module */

import { registry } from "@web/core/registry";
import { Component, useState, onWillStart } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { callWithPocketAuth } from "./pocket_rpc";

export class SeerbitDashboard extends Component {
    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.dialog = useService("dialog");
        this.state = useState({
            odoo_balance: 0,
            platform_balance: 0,
            difference: 0,
            needs_reconciliation: false,
            currency_symbol: '₦',
            recent_transactions: [],
            journal_id: false,
            loading: true,
            loading_pocket: true,
            filter_24h: true,
        });

        onWillStart(async () => {
            await this.fetchDashboardData();
        });
    }

    async fetchDashboardData() {
        this.state.loading = true;
        try {
            const data = await this.orm.call("seerbit.dashboard.backend", "get_dashboard_data", [this.state.filter_24h]);
            Object.assign(this.state, data);
            this.fetchPocketBalance();
        } catch (error) {
            console.error("Failed to load dashboard data", error);
        } finally {
            this.state.loading = false;
        }
    }

    async fetchPocketBalance() {
        this.state.loading_pocket = true;
        try {
            const platform_balance = await callWithPocketAuth(this.orm, this.dialog, "seerbit.dashboard.backend", "get_pocket_balance", []);
            this.state.platform_balance = platform_balance;
            this.state.difference = Math.abs(platform_balance - this.state.odoo_balance);
            this.state.needs_reconciliation = this.state.difference > 0.01;
        } catch (error) {
            console.error("Failed to fetch pocket balance", error);
            this.state.platform_balance = 0;
        } finally {
            this.state.loading_pocket = false;
        }
    }

    async refresh() {
        await this.fetchDashboardData();
    }

    async toggleFilter24h() {
        this.state.filter_24h = !this.state.filter_24h;
        await this.fetchDashboardData();
    }

    openPayouts() {
        this.action.doAction({
            type: 'ir.actions.act_window',
            name: 'New Payout',
            res_model: 'seerbit.payout',
            views: [[false, 'form']],
            target: 'current',
        });
    }

    openReconcile() {
        if (this.state.journal_id) {
            this.action.doAction({
                type: 'ir.actions.act_window',
                name: 'Bank Statements',
                res_model: 'account.bank.statement',
                views: [[false, 'list'], [false, 'form']],
                domain: [['journal_id', '=', this.state.journal_id]],
            });
        }
    }

    openSeerbitEntries() {
        if (this.state.journal_id) {
            this.action.doAction({
                type: 'ir.actions.act_window',
                name: 'Seerbit Entries',
                res_model: 'account.move',
                views: [[false, 'list'], [false, 'form']],
                domain: [['journal_id', '=', this.state.journal_id], ['state', '=', 'posted']],
            });
        }
    }

    openAllTransactions() {
        if (this.state.journal_id) {
            this.action.doAction({
                type: 'ir.actions.act_window',
                name: 'All Transactions',
                res_model: 'account.payment',
                views: [[false, 'list'], [false, 'form']],
                domain: [['journal_id', '=', this.state.journal_id]],
            });
        }
    }

    openTransaction(id) {
        this.action.doAction({
            type: 'ir.actions.act_window',
            name: 'Transaction',
            res_model: 'account.payment',
            views: [[false, 'form']],
            res_id: id,
        });
    }

    reconcileTx(id) {
        this.action.doAction({
            type: 'ir.actions.act_window',
            name: 'Reconcile Transaction',
            res_model: 'account.payment',
            views: [[false, 'form']],
            res_id: id,
            context: {
                'action_to_trigger': 'action_open_reconcile'
            }
        });
    }
}

SeerbitDashboard.template = "pos_seerbit.SeerbitDashboard";

registry.category("actions").add("seerbit_dashboard", SeerbitDashboard);

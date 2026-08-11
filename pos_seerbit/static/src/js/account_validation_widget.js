/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, useState, onMounted, onWillUnmount, useEffect } from "@odoo/owl";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { useService } from "@web/core/utils/hooks";
import { callWithPocketAuth } from "./pocket_rpc";

export class SeerbitAccountName extends Component {
    setup() {
        this.orm = useService("orm");
        this.dialog = useService("dialog");
        this.state = useState({
            loading: false,
            verified: false,
            error: false,
        });

        this.timeout = null;
        this.inputTimeout = null;
        this.accountInput = null;

        onMounted(() => {
            // Find the account_number input in the DOM to listen to typing
            this.accountInput = document.querySelector('div[name="account_number"] input') || document.querySelector('input[name="account_number"]');
            if (this.accountInput) {
                this.accountInput.addEventListener('input', this.onAccountInput.bind(this));
            }
        });

        onWillUnmount(() => {
            if (this.accountInput) {
                this.accountInput.removeEventListener('input', this.onAccountInput.bind(this));
            }
        });

        useEffect(
            () => {
                const accNumber = this.props.record.data.account_number;
                const bankCode = this.props.record.data.bank_code;
                
                clearTimeout(this.timeout);
                
                if (accNumber && accNumber.length >= 5 && bankCode) {
                    this.state.loading = true;
                    this.state.verified = false;
                    this.state.error = false;
                    
                    this.timeout = setTimeout(() => {
                        this.verifyAccount(accNumber);
                    }, 500);
                } else if (!accNumber || accNumber.length < 5) {
                    this.state.loading = false;
                    this.state.verified = false;
                    this.state.error = false;
                }
            },
            () => [this.props.record.data.account_number, this.props.record.data.bank_code]
        );
    }

    onAccountInput(ev) {
        const val = ev.target.value;
        clearTimeout(this.inputTimeout);
        this.inputTimeout = setTimeout(() => {
            this.props.record.update({ account_number: val });
        }, 300);
    }

    async verifyAccount(accNumber) {
        const bankCode = this.props.record.data.bank_code;
        if (!bankCode) {
            this.state.loading = false;
            return;
        }

        try {
            const res = await callWithPocketAuth(this.orm, this.dialog, "seerbit.payout", "verify_account_api", [accNumber, bankCode]);
            
            this.state.loading = false;
            if (res && res.account_name) {
                this.state.verified = true;
                this.state.error = false;
                this.props.record.update({ 
                    [this.props.name]: res.account_name,
                    is_account_verified: true,
                    verified_account_name: res.account_name
                });
            } else {
                this.state.verified = false;
                this.state.error = true;
                this.props.record.update({ 
                    [this.props.name]: "Unverified or Not Found",
                    is_account_verified: false,
                    verified_account_name: false
                });
            }
        } catch (error) {
            this.state.loading = false;
            this.state.verified = false;
            this.state.error = true;
            this.props.record.update({ 
                [this.props.name]: "Unverified or Not Found",
                is_account_verified: false,
                verified_account_name: false
            });
        }
    }
}
SeerbitAccountName.template = "pos_seerbit.AccountNameWidget";
SeerbitAccountName.props = { ...standardFieldProps };

registry.category("fields").add("seerbit_account_name", {
    component: SeerbitAccountName,
    supportedTypes: ["char"],
});

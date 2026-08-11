/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, useState, onWillStart, useEffect } from "@odoo/owl";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { callWithPocketAuth } from "./pocket_rpc";

export class SeerbitBankSelection extends Component {
    setup() {
        this.orm = useService("orm");
        this.dialog = useService("dialog");
        this.state = useState({
            loading: true,
            banks: [],
            error: false,
        });

        onWillStart(() => {
            this.loadBanks();
        });

        useEffect(
            () => {
                this.autoMatchBank();
            },
            () => [this.props.record.data[this.props.name], this.state.banks.length]
        );
    }

    autoMatchBank() {
        const currentName = this.props.record.data[this.props.name];
        if (currentName && this.state.banks.length > 0) {
            const exactMatch = this.state.banks.find(b => b.bankName === currentName);
            if (!exactMatch) {
                try {
                    const currentLower = currentName.toLowerCase();
                    const words = currentLower.split(/\s+/).filter(w => w.length > 2);
                    
                    let bestMatch = null;
                    let maxMatches = 0;
                    
                    for (const b of this.state.banks) {
                        const bName = b.bankName.toLowerCase();
                        
                        // Exact substring match check
                        if (currentLower.includes(bName) || bName.includes(currentLower)) {
                            bestMatch = b;
                            break; // Perfect partial match
                        }
                        
                        // Word overlap check
                        let matches = 0;
                        for (const word of words) {
                            if (bName.includes(word)) matches++;
                        }
                        
                        if (matches > maxMatches) {
                            maxMatches = matches;
                            bestMatch = b;
                        }
                    }

                    if (bestMatch) {
                        this.props.record.update({
                            [this.props.name]: bestMatch.bankName,
                            bank_code: bestMatch.bankCode
                        });
                    }
                } catch (e) {
                    console.error("Fuzzy match failed", e);
                }
            }
        }
    }

    async loadBanks() {
        try {
            this.state.loading = true;
            this.state.error = false;
            const banks = await callWithPocketAuth(this.orm, this.dialog, "seerbit.payout", "get_seerbit_banks", []);
            this.state.banks = banks || [];
        } catch (error) {
            console.error("Failed to load Seerbit banks", error);
            this.state.error = true;
            this.state.banks = [];
        } finally {
            this.state.loading = false;
        }
    }

    onChange(ev) {
        const selectedBankCode = ev.target.value;
        const selectedBank = this.state.banks.find(b => b.bankCode === selectedBankCode);
        
        if (selectedBank) {
            this.props.record.update({
                [this.props.name]: selectedBank.bankName,
                bank_code: selectedBank.bankCode
            });
        } else {
            this.props.record.update({
                [this.props.name]: false,
                bank_code: false
            });
        }
    }

    get selectedValue() {
        const currentName = this.props.record.data[this.props.name];
        const bank = this.state.banks.find(b => b.bankName === currentName);
        return bank ? bank.bankCode : "";
    }
}

SeerbitBankSelection.template = "pos_seerbit.BankSelectionWidget";
SeerbitBankSelection.props = {
    ...standardFieldProps,
};

registry.category("fields").add("seerbit_bank_selection", {
    component: SeerbitBankSelection,
    supportedTypes: ["char"],
});

/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { useService } from "@web/core/utils/hooks";
import { session } from "@web/session";

function pocketSessionKey(suffix) {
    const companyId = session.user_companies?.current_company || session.company_id || 0;
    return `seerbit_pocket_${suffix}_${companyId}`;
}

export class PocketAuthModal extends Component {
    setup() {
        this.orm = useService("orm");
        this.state = useState({
            email: sessionStorage.getItem(pocketSessionKey("email")) || "",
            password: "",
            loading: false,
            error: false,
            requirePasswordChange: false,
        });
    }

    onKeyUp(ev) {
        if (ev.key === "Enter") {
            this.authenticate();
        }
    }

    async authenticate() {
        if (!this.state.email || !this.state.password) {
            this.state.error = "Email and Password are required.";
            return;
        }

        this.state.loading = true;
        this.state.error = false;

        try {
            const result = await this.orm.silent.call(
                "seerbit.payout",
                "authenticate_pocket",
                [this.state.email, this.state.password]
            );

            if (result && result.requirePasswordChange) {
                this.state.requirePasswordChange = true;
                this.state.loading = false;
                return;
            }

            sessionStorage.setItem(pocketSessionKey("email"), this.state.email);
            sessionStorage.setItem(pocketSessionKey("password"), this.state.password);

            this.props.onSuccess();
            this.props.close();

        } catch (error) {
            this.state.error = error.data?.message || "Authentication failed. Please check your credentials.";
            this.state.loading = false;
        }
    }
}

PocketAuthModal.components = { Dialog };
PocketAuthModal.template = "pos_seerbit.PocketAuthModal";

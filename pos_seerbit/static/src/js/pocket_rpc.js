/** @odoo-module **/

import { session } from "@web/session";
import { PocketAuthModal } from "./pocket_auth_modal";

function pocketSessionKey(suffix) {
    const companyId = session.user_companies?.current_company || session.company_id || 0;
    return `seerbit_pocket_${suffix}_${companyId}`;
}

/**
 * Wraps an ORM call to automatically handle Pocket API authentication.
 * If POCKET_AUTH_REQUIRED is thrown, it attempts silent re-auth or pops up the modal.
 * Credentials in sessionStorage are scoped per company.
 */
export async function callWithPocketAuth(orm, dialog, model, method, args = [], kwargs = {}) {
    try {
        return await orm.silent.call(model, method, args, kwargs);
    } catch (error) {
        const errorMsg = error.data?.message || error.message || "";
        if (errorMsg.includes("POCKET_AUTH_REQUIRED")) {
            const savedEmail = sessionStorage.getItem(pocketSessionKey("email"));
            const savedPassword = sessionStorage.getItem(pocketSessionKey("password"));

            if (savedEmail && savedPassword) {
                try {
                    await orm.silent.call("seerbit.payout", "authenticate_pocket", [savedEmail, savedPassword]);
                    return await orm.silent.call(model, method, args, kwargs);
                } catch (reAuthError) {
                    console.warn("Silent re-auth failed, opening modal.");
                }
            } else {
                try {
                    const configAuth = await orm.silent.call("seerbit.payout", "authenticate_pocket_with_config", []);
                    if (configAuth) {
                        return await orm.silent.call(model, method, args, kwargs);
                    }
                } catch (configAuthError) {
                    console.warn("Config silent auth failed, opening modal.", configAuthError);
                }
            }

            return new Promise((resolve, reject) => {
                dialog.add(PocketAuthModal, {
                    onSuccess: async () => {
                        try {
                            const result = await orm.silent.call(model, method, args, kwargs);
                            resolve(result);
                        } catch (err) {
                            reject(err);
                        }
                    }
                });
            });
        }

        throw error;
    }
}

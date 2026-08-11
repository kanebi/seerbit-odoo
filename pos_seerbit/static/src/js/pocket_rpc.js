/** @odoo-module **/

import { PocketAuthModal } from "./pocket_auth_modal";

/**
 * Wraps an ORM call to automatically handle Pocket API authentication.
 * If POCKET_AUTH_REQUIRED is thrown, it attempts silent re-auth or pops up the modal.
 * 
 * @param {Object} orm - The odoo orm service
 * @param {Object} dialog - The odoo dialog service
 * @param {String} model - Model name
 * @param {String} method - Method name
 * @param {Array} args - Method arguments
 * @param {Object} kwargs - Method kwargs
 */
export async function callWithPocketAuth(orm, dialog, model, method, args = [], kwargs = {}) {
    try {
        // Use orm.silent.call to prevent Odoo from popping up a global error dialog
        return await orm.silent.call(model, method, args, kwargs);
    } catch (error) {
        const errorMsg = error.data?.message || error.message || "";
        if (errorMsg.includes("POCKET_AUTH_REQUIRED")) {
            // Check for saved credentials
            const savedEmail = sessionStorage.getItem("seerbit_pocket_email");
            const savedPassword = sessionStorage.getItem("seerbit_pocket_password");

            if (savedEmail && savedPassword) {
                try {
                    // Try silent re-auth
                    await orm.silent.call("seerbit.payout", "authenticate_pocket", [savedEmail, savedPassword]);
                    // Retry original call
                    return await orm.silent.call(model, method, args, kwargs);
                } catch (reAuthError) {
                    console.warn("Silent re-auth failed, opening modal.");
                }
            } else {
                // Try silent auth from config
                try {
                    const configAuth = await orm.silent.call("seerbit.payout", "authenticate_pocket_with_config", []);
                    if (configAuth) {
                        return await orm.silent.call(model, method, args, kwargs);
                    }
                } catch (configAuthError) {
                    console.warn("Config silent auth failed, opening modal.", configAuthError);
                }
            }

            // Open Auth Modal
            return new Promise((resolve, reject) => {
                dialog.add(PocketAuthModal, {
                    onSuccess: async () => {
                        try {
                            // Retry original call after successful manual auth
                            const result = await orm.silent.call(model, method, args, kwargs);
                            resolve(result);
                        } catch (err) {
                            reject(err);
                        }
                    }
                });
            });
        }
        
        // Throw normal errors
        throw error;
    }
}

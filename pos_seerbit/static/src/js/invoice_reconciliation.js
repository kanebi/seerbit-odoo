/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, onWillStart, onWillUnmount, useState } from "@odoo/owl";
import FirebaseInit from "./firebase_init";

export class SeerbitInvoiceListener extends Component {
    static template = "pos_seerbit.InvoicePaymentListener";
    static props = ["*"];

    setup() {
        this.actionService = useService("action");
        this.orm = useService("orm");
        this.notification = useService("notification");
        
        this.state = useState({
            status: "waiting",
            message: "Initializing Firestore...",
        });

        this.unsubscribe = null;

        onWillStart(async () => {
            await this.initFirebase();
        });

        onWillUnmount(() => {
            if (this.unsubscribe) {
                this.unsubscribe();
            }
        });
    }

    async initFirebase() {
        try {
            const success = await FirebaseInit.initializeFirebase(this.orm);
            if (success) {
                this.state.message = "Waiting for terminal payment... Please complete the transaction on the POS device.";
                this.startListening();
            } else {
                this.state.status = "error";
                this.state.message = "Failed to initialize Firestore connection. Please check your POS configuration settings.";
            }
        } catch (error) {
            this.state.status = "error";
            this.state.message = "Error initializing Firebase: " + error.message;
        }
    }

    startListening() {
        const invoiceId = this.props.action.params.invoice_id;
        const terminalId = this.props.action.params.terminal_id;
        const firestoreDb = FirebaseInit.getFirestoreDb();
        
        if (!firestoreDb) {
            this.state.status = "error";
            this.state.message = "Firestore database is not available.";
            return;
        }

        console.log(`Starting Firestore listener for Invoice ID: ${invoiceId} on terminal: ${terminalId}`);

        // Query the reconciliations collection for this invoice
        const query = firestoreDb.collection("reconciliations")
            .where("id", "==", String(invoiceId))
            .limit(1);

        this.unsubscribe = query.onSnapshot(
            async (snapshot) => {
                if (snapshot.empty) {
                    console.log("No matching reconciliation yet.");
                    return;
                }

                snapshot.docChanges().forEach(async (change) => {
                    if (change.type === "added") {
                        const data = change.doc.data();
                        console.log("Reconciliation snapshot received:", data);
                        
                        const status = String(data?.status || "").toLowerCase();
                        if (["success", "completed", "complete", "done", "successful"].includes(status)) {
                            this.state.status = "success";
                            this.state.message = "Payment successful! Reconciling with invoice...";
                            
                            if (this.unsubscribe) {
                                this.unsubscribe();
                                this.unsubscribe = null;
                            }
                            
                            await this.reconcileInvoice(data);
                        }
                    }
                });
            },
            (error) => {
                console.error("Firestore listener error:", error);
                this.state.status = "error";
                this.state.message = "Connection lost: " + error.message;
            }
        );
    }

    async reconcileInvoice(data) {
        const invoiceId = this.props.action.params.invoice_id;
        const transactionRef = data.sessionId || data.transactionRef || data.id;
        const amount = data.transactionValue || data.amount;
        
        try {
            // Call the backend to create and reconcile the payment
            const result = await this.orm.call(
                "account.move",
                "action_process_seerbit_pos_payment",
                [[parseInt(invoiceId)]],
                {
                    transaction_ref: transactionRef,
                    amount: parseFloat(amount),
                }
            );

            if (result) {
                this.state.status = "success";
                this.state.message = "Payment successful! Reconciled and saved to Odoo.";
                
                this.notification.add("Payment reconciled successfully!", {
                    type: "success",
                });
                
                // Close the modal/action and redirect back to the invoice view after a short visual delay
                setTimeout(() => {
                    this.actionService.doAction({
                        type: 'ir.actions.act_window',
                        res_model: 'account.move',
                        res_id: parseInt(invoiceId),
                        views: [[false, 'form']],
                        target: 'current',
                    });
                }, 2500);
            } else {
                this.state.status = "error";
                this.state.message = "Failed to process payment in Odoo backend.";
            }
        } catch (error) {
            console.error("Reconciliation error:", error);
            this.state.status = "error";
            this.state.message = "Error reconciling payment: " + error.message;
        }
    }

    onCancel() {
        if (this.unsubscribe) {
            this.unsubscribe();
            this.unsubscribe = null;
        }
        // Navigate back to the invoice form view
        this.actionService.doAction({
            type: 'ir.actions.act_window',
            res_model: 'account.move',
            res_id: parseInt(this.props.action.params.invoice_id),
            views: [[false, 'form']],
            target: 'current',
        });
    }

    async onResend() {
        const invoiceId = this.props.action.params.invoice_id;
        const terminalId = this.props.action.params.terminal_id;
        
        this.state.message = "Resending payment request to POS terminal...";
        
        try {
            const result = await this.orm.call(
                "account.move",
                "action_resend_seerbit_pos_payment",
                [[parseInt(invoiceId)]],
                {
                    terminal_id: String(terminalId),
                }
            );
            if (result) {
                this.state.message = "Payment request resent successfully! Waiting for terminal payment...";
                this.notification.add("Payment request resent successfully!", {
                    type: "success",
                });
            } else {
                this.notification.add("Failed to resend payment request.", {
                    type: "danger",
                });
            }
        } catch (error) {
            console.error("Resend error:", error);
            this.notification.add("Error resending request: " + error.message, {
                type: "danger",
            });
            this.state.message = "Error resending request: " + error.message;
        }
    }
}

registry.category("actions").add("seerbit_invoice_reconciliation_listener", SeerbitInvoiceListener);

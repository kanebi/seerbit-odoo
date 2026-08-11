/** @odoo-module **/

import { PaymentScreen } from '@point_of_sale/app/screens/payment_screen/payment_screen';
import { patch } from '@web/core/utils/patch';
import { onMounted, onWillUnmount } from '@odoo/owl';

// Patch PaymentScreen to handle Seerbit payment line status
patch(PaymentScreen.prototype, {
    setup() {
        super.setup();
        onMounted(() => {
            // Set pending Seerbit payments to waiting status
            const pendingPaymentLine = this.env.services.pos.getPendingPaymentLine('seerbit')
            if (pendingPaymentLine) {
                console.log('Found pending Seerbit line')
                pendingPaymentLine.set_payment_status('waitingSeerbit');
            }
        });

        onWillUnmount(() => {
            // When leaving the payment screen, ensure any Seerbit processes are stopped.
            this.paymentLines.forEach(line => {
                console.log('Checking payment line:', line);
                if (line.payment_method_id.use_payment_terminal === 'seerbit') {
                    console.log('Found Seerbit payment line');
                    // The payment_terminal is the SeerbitPayment instance
                    if (line.payment_method_id.payment_terminal) {
                        console.log('Closing Seerbit payment line');
                        line.payment_method_id.payment_terminal.close();
                    }
                }
            });
        });
    },

    
    async sendForceDone(line) {
        const payment_terminal = line.payment_method_id.payment_terminal;
         await payment_terminal.send_force_done(
            line
        );
    },
    paymentMethodImage(id) {
        if (this.paymentMethod.use_payment_terminal === "seerbit") {
            return "/pos_seerbit/static/description/icon.png";
        }
        if (this.paymentMethod.image) {
            return `/web/image/pos.payment.method/${id}/image`;
        } else if (this.paymentMethod.type === "cash") {
            return "/point_of_sale/static/src/img/money.png";
        } else if (this.paymentMethod.type === "pay_later") {
            return "/point_of_sale/static/src/img/pay-later.png";
        }  else {
            return "/point_of_sale/static/src/img/card-bank.png";
        }
    },
    deletePaymentLine(uuid) {
        const line = this.paymentLines.find( (line) => line.uuid === uuid);
        if (line.payment_method_id.payment_method_type === "qr_code") {
            this.currentOrder.remove_paymentline(line);
            this.numberBuffer.reset();
            return;
        }
        if (["waiting", "waitingSeerbit", "waitingCard", "timeout"].includes(line.get_payment_status()) && line.payment_method_id.payment_terminal) {
            line.set_payment_status("waitingCancel");
            this.sendPaymentCancel(line).then( () => {
            line.payment_method_id.payment_terminal.send_payment_cancel(this.currentOrder, uuid).then( () => {
                this.currentOrder.remove_paymentline(line);
                this.numberBuffer.reset();
            }
            );
        })
        } else if (line.get_payment_status() !== "waitingCancel") {
            this.currentOrder.remove_paymentline(line);
            this.numberBuffer.reset();
        }
    }

    
});
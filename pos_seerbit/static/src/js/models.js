/** @odoo-module **/

import { register_payment_method } from "@point_of_sale/app/services/pos_store";
import PaymentSeerbit from "./payment_seerbit";

register_payment_method("seerbit", PaymentSeerbit);

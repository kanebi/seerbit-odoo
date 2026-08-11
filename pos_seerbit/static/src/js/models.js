/** @odoo-module **/

import { register_payment_method } from '@point_of_sale/app/store/pos_store';
import PaymentSeerbit from './payment_seerbit';

// Register Seerbit payment method with POS store
    register_payment_method('seerbit', PaymentSeerbit);

    
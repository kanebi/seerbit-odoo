# -*- coding: utf-8 -*-
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Drop leftover field groups on seerbit_latest_response (was erp_manager / pos_user).

    Field ``groups=`` restrictions persist on ir.model.fields after code removal
    unless explicitly cleared; POS UI load then ACL-denies terminal cashiers.
    """
    cr.execute(
        """
        DELETE FROM ir_model_fields_group_rel
         WHERE field_id IN (
               SELECT id FROM ir_model_fields
                WHERE model = 'pos.payment.method'
                  AND name = 'seerbit_latest_response'
         )
        """
    )
    _logger.info(
        "pos_seerbit 0.1.9: cleared field groups on pos.payment.method.seerbit_latest_response (%s rows)",
        cr.rowcount,
    )

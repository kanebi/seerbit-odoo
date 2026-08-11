# -*- coding: utf-8 -*-
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Provision Seerbit journal + POS PM for every company (existing installs)."""
    from odoo import api, SUPERUSER_ID
    from odoo.addons.pos_seerbit.hooks import ensure_seerbit_setup_all_companies

    env = api.Environment(cr, SUPERUSER_ID, {})
    try:
        ensure_seerbit_setup_all_companies(env)
        _logger.info("pos_seerbit 0.2.5: ensured Seerbit journal/PM for all companies")
    except Exception as e:
        _logger.error("pos_seerbit 0.2.5: multi-company Seerbit setup failed: %s", e)

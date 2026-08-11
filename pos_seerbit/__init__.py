# coding: utf-8
from . import controllers, models, services, wizard
from . import utils
from .hooks import post_init_hook

# Note: Configuration is now managed through Odoo settings
# No need to validate on module load as it's handled by the settings model

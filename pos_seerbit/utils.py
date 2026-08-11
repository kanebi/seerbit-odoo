"""
Utility functions for Seerbit Odoo integration.
These functions are isolated from Odoo dependencies for easier testing.
"""


def format_erp_ref(ref):
    """
    Format ERP reference to ensure consistent format with 'odoo_' prefix.

    Args:
        ref (str): The ERP reference to format

    Returns:
        str: Formatted reference with 'odoo_' prefix, normalized to lowercase,
             with whitespace removed, or empty string if ref is None/empty
    """
    if not ref:
        return ''

    # Remove whitespace and convert to lowercase
    normalized = ref.strip().lower().replace(' ', '')

    # If after normalization we have nothing, return empty string
    if not normalized:
        return ''

    # Add prefix if not already present
    if not normalized.startswith('odoo_'):
        normalized = f'odoo_{normalized}'

    return normalized
 
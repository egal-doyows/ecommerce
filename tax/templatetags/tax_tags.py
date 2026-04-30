from decimal import Decimal

from django import template

from tax.models import TaxConfiguration

register = template.Library()


@register.simple_tag
def tax_breakdown(subtotal):
    """
    Returns a dict with subtotal, tax_amount, total, tax_name, tax_rate, is_enabled.
    Usage: {% tax_breakdown cart.get_total as tax %}
    Then use {{ tax.subtotal }}, {{ tax.tax_amount }}, {{ tax.total }}, etc.
    """
    tax_config = TaxConfiguration.load()
    display_subtotal, tax_amount, total = tax_config.calculate(Decimal(str(subtotal)))
    return {
        'subtotal': display_subtotal,
        'tax_amount': tax_amount,
        'total': total,
        'tax_name': tax_config.tax_name,
        'tax_rate': tax_config.tax_rate,
        'is_enabled': tax_config.is_enabled,
        'tax_number': tax_config.tax_number,
        'tax_type': tax_config.tax_type,
    }

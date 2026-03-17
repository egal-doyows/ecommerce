from django import template

register = template.Library()


@register.filter
def get_balance(account_pk, balances_dict):
    """Look up an account's balance from the balances dict. Usage: {{ pk|get_balance:balances }}"""
    try:
        val = balances_dict.get(int(account_pk), 0)
        return f'{val:,.0f}'
    except (ValueError, TypeError, AttributeError):
        return '0'

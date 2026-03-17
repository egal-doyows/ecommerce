from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def active_nav(context, *url_names):
    """Return 'active' class if current URL matches any of the given names."""
    request = context.get('request')
    if request and hasattr(request, 'resolver_match') and request.resolver_match:
        if request.resolver_match.url_name in url_names:
            return 'active'
    return ''
